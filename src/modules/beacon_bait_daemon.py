import threading
import time
import json
import os
import yaml
import logging
from scapy.all import sendp, sniff
from scapy.layers.dot11 import RadioTap, Dot11, Dot11Beacon, Dot11Elt
from scapy.layers.dot11 import Dot11ProbeReq, Dot11Auth
from dotenv import load_dotenv

# Import hệ thần kinh Actuators để gọi lệnh SSH
import src.modules.wifi_actuators as actuators

logging.basicConfig(level=logging.INFO, format='%(message)s')

# ==========================================
# 1. CẤU HÌNH THÔNG SỐ VÀ ĐƯỜNG DẪN
# ==========================================
load_dotenv()
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")

VIP_MAC_FILE = "data/processed/mac_vip_list.json"
CONFIG_PATH = "configs/network_config.yaml"

IFACE = "wlan0mon"  
FAKE_BSSID = "00:11:22:33:44:55" 
FAKE_SSID = "School_Teacher" # Phát mồi nhử trùng tên với mạng thật

# Thời gian duy trì mạng Teacher sau khi đánh thức (VD: 30 phút = 1800s)
# Trong thi KHKT để test nhanh, bạn có thể để 60s
WAKE_DURATION = 60 

# Từ điển theo dõi trạng thái mạng của từng AP: { "192.168.1.2": {"is_woken": False, "timer": None} }
ap_status = {}

def load_ap_map():
    """Tự động tải danh sách IP của tất cả AP từ file YAML"""
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
            return config.get('network_topology', {}).get('ap_map', {})
    except Exception as e:
        logging.error(f"[-] Lỗi đọc file YAML: {e}")
        return {}

AP_MAP = load_ap_map()
for mac, ip in AP_MAP.items():
    ap_status[ip] = {"is_woken": False, "timer": None}

def get_vip_macs():
    """Đọc danh sách VIP liên tục từ JSON do trang Onboarding tạo ra"""
    try:
        if os.path.exists(VIP_MAC_FILE):
            with open(VIP_MAC_FILE, 'r') as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()

# ==========================================
# 2. LUỒNG 1: BƠM GÓI TIN MỒI NHỬ (INJECTOR)
# ==========================================
def inject_fake_beacon():
    """
    Tạo và phát khung tin Beacon mồi nhử.
    Tận dụng tính năng Packet Injection của card RTL8812AU.
    """
    dot11 = Dot11(type=0, subtype=8, addr1="ff:ff:ff:ff:ff:ff", addr2=FAKE_BSSID, addr3=FAKE_BSSID)
    beacon = Dot11Beacon(cap="ESS+privacy") 
    essid = Dot11Elt(ID="SSID", info=FAKE_SSID, len=len(FAKE_SSID))
    frame = RadioTap()/dot11/beacon/essid
    
    logging.info(f"[*] Radar Mồi nhử đang phát sóng '{FAKE_SSID}' mỗi 30 giây...")
    while True:
        # Bắn 3 gói mồi nhử ra không khí
        sendp(frame, iface=IFACE, count=3, verbose=False)
        time.sleep(30)

# ==========================================
# 3. LUỒNG 2: NGHE LÉN VÀ KÍCH HOẠT (SNIFFER)
# ==========================================
def turn_off_teacher_network(target_ip):
    """Hàm chạy ngầm: Tắt mạng Teacher sau khi hết giờ để nhường sóng cho Học sinh"""
    logging.info(f"[!] Hết thời gian VIP. Đang đóng mạng Teacher trên AP {target_ip}...")
    actuators.prune_unused_ssid(target_ip, USER, PWD, vap_interface="teacher_radio0", disable=True)
    ap_status[target_ip]["is_woken"] = False

def wake_up_router(client_mac, bssid_receiver):
    """Kích hoạt mạng Teacher trên ĐÚNG cục AP mà giáo viên đang đứng gần"""
    target_ip = AP_MAP.get(bssid_receiver)
    if not target_ip: return
    
    status = ap_status[target_ip]
    
    # Nếu mạng AP đó đang tắt, bật nó lên!
    if not status["is_woken"]:
        logging.info(f"🚀 PHÁT HIỆN VIP ({client_mac}) TẠI AP {target_ip}. ĐANG MỞ CỔNG KHÔNG GIAN...")
        status["is_woken"] = True
        
        # Gọi Actuator bật mạng (truyền tham số disable=False)
        actuators.prune_unused_ssid(target_ip, USER, PWD, vap_interface="teacher_radio0", disable=False)
        
        # Bơm công suất lên mức max (20dBm) để thiết bị GV bắt sóng mạnh nhất
        actuators.optimize_tx_power(target_ip, USER, PWD, interface="wlan0", target_power_dbm=20)
        
        # Khởi tạo hẹn giờ tắt mạng
        timer = threading.Timer(WAKE_DURATION, turn_off_teacher_network, args=[target_ip])
        timer.start()
        status["timer"] = timer
    else:
        # Nếu mạng đang bật mà VIP vẫn ở đó, Reset lại đồng hồ đếm ngược
        if status["timer"]:
            status["timer"].cancel()
        timer = threading.Timer(WAKE_DURATION, turn_off_teacher_network, args=[target_ip])
        timer.start()
        status["timer"] = timer

def process_packet(packet):
    """Đánh hơi Probe Request hoặc Authentication Frame"""
    # Nếu gói tin là Probe Request (Tìm mạng) hoặc Auth (Xin vào mạng)
    if packet.haslayer(Dot11ProbeReq) or packet.haslayer(Dot11Auth):
        client_mac = packet.addr2
        bssid_receiver = packet.addr1 # Đích đến của gói tin (Có thể là Broadcast hoặc đích danh AP)
        
        if client_mac:
            client_mac = client_mac.upper()
            vip_macs = get_vip_macs()
            
            # KIỂM TRA ĐỊNH DANH
            if client_mac in vip_macs:
                # Tìm xem gói tin này đang gửi cho AP nào
                if bssid_receiver and bssid_receiver.upper() in AP_MAP:
                    threading.Thread(target=wake_up_router, args=(client_mac, bssid_receiver.upper())).start()
                # Nếu gửi Broadcast (Tìm mù), bật AP gần nhất (Trong phạm vi KHKT, ta có thể bật Master)
                elif bssid_receiver == "FF:FF:FF:FF:FF:FF":
                    master_mac = list(AP_MAP.keys())[0] # Lấy AP đầu tiên làm mặc định
                    threading.Thread(target=wake_up_router, args=(client_mac, master_mac)).start()

def sniff_responses():
    logging.info("[*] Vệ sĩ Mồi nhử đang lắng nghe phản hồi 24/7...")
    sniff(iface=IFACE, prn=process_packet, store=0)

# ==========================================
# KHỞI CHẠY HỆ THỐNG
# ==========================================
if __name__ == "__main__":
    if not AP_MAP:
        logging.critical("[-] Lỗi: File network_config.yaml trống. Hãy chạy auto_discover_aps.py trước!")
        exit(1)
        
    threading.Thread(target=inject_fake_beacon, daemon=True).start()
    sniff_responses()