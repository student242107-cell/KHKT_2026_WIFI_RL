import threading
import time
import json
import os
import yaml
import logging
from scapy.all import sendp, sniff
from scapy.layers.dot11 import RadioTap, Dot11, Dot11Beacon, Dot11Elt
from scapy.layers.dot11 import Dot11ProbeReq, Dot11Auth, Dot11AssoReq
from dotenv import load_dotenv

import src.modules.wifi_actuators as actuators

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [BEACON-BAIT] - %(message)s')

load_dotenv()
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS", "")

VIP_MAC_FILE = "data/processed/mac_vip_list.json"
CONFIG_PATH = "configs/network_config.yaml"

IFACE = os.getenv("MONITOR_IFACE", "wlan0mon")
FAKE_BSSID = "02:11:22:33:44:55"
FAKE_SSID = os.getenv("TEACHER_SSID", "School_Teacher")

# Thời gian chờ kiểm tra sau khi không còn thấy tín hiệu (60s khi thi KHKT, 600s thực tế)
WAKE_DURATION = 60
INJECT_INTERVAL = 25

ap_status = {}
AP_MAP = {}
PRIVILEGED_OFFICE_IPS = set()


def load_topology_config():
    """Đọc danh sách AP và phân loại vùng Classroom vs. Privileged Office (Always-On)."""
    global AP_MAP, PRIVILEGED_OFFICE_IPS
    if not os.path.exists(CONFIG_PATH):
        return
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
            topo = config.get('network_topology', {})
            for bssid, ip in topo.get('ap_map', {}).items():
                AP_MAP[bssid.upper()] = ip
            for bssid, ip in topo.get('ap_map_2g', {}).items():
                AP_MAP[bssid.upper()] = ip

            for node in topo.get('nodes', []):
                ip = node.get('ip')
                ztype = node.get('zone_type', 'classroom').lower()
                if ztype == 'privileged_office':
                    PRIVILEGED_OFFICE_IPS.add(ip)
                if ip and ip not in ap_status:
                    ap_status[ip] = {
                        "name": node.get("name", ip),
                        "zone_type": ztype,
                        "is_woken": (ztype == 'privileged_office'),
                        "timer": None
                    }
    except Exception as e:
        logging.error(f"[-] Lỗi đọc file YAML: {e}")


load_topology_config()


def get_vip_macs():
    try:
        if os.path.exists(VIP_MAC_FILE):
            with open(VIP_MAC_FILE, 'r') as f:
                return set(m.upper() for m in json.load(f))
    except Exception:
        pass
    return set()


# ==========================================
# LUỒNG 1: BƠM GÓI TIN MỒI NHỬ QUA USB WI-FI
# ==========================================
def inject_fake_beacon():
    """
    Bơm khung tin 802.11 Beacon mồi nhử của mạng 'School_Teacher' mỗi 25 giây
    trên card USB Wi-Fi Monitor Mode để kích thích điện thoại Giáo viên phát Probe/Auth.
    """
    dot11 = Dot11(type=0, subtype=8, addr1="ff:ff:ff:ff:ff:ff", addr2=FAKE_BSSID, addr3=FAKE_BSSID)
    beacon = Dot11Beacon(cap="ESS+privacy")
    essid = Dot11Elt(ID="SSID", info=FAKE_SSID, len=len(FAKE_SSID))
    frame = RadioTap() / dot11 / beacon / essid

    logging.info(f"[📡] USB Wi-Fi Injector đang bơm mồi nhử '{FAKE_SSID}' trên {IFACE} mỗi {INJECT_INTERVAL}s...")
    while True:
        try:
            sendp(frame, iface=IFACE, count=5, inter=0.05, verbose=False)
        except Exception as e:
            logging.warning(f"[!] Không thể bơm gói tin trên {IFACE} (Kiểm tra USB Wi-Fi Monitor Mode): {e}")
        time.sleep(INJECT_INTERVAL)


# ==========================================
# LUỒNG 2: ĐÁNH THỨC & TỈA SSID AN TOÀN (USER-CENTRIC)
# ==========================================
def try_turn_off_teacher_network(target_ip):
    """
    Khi hết thời gian đếm ngược:
    - Nếu là vùng 'privileged_office' (Phòng Giáo viên): Luôn bật (Always-On), không bao giờ tắt.
    - Nếu là vùng 'classroom': Kiểm tra xem còn Giáo viên nào đang kết nối không.
      + Nếu còn >= 1 GV: Gia hạn thêm WAKE_DURATION giây!
      + Nếu = 0 GV (Giáo viên đã rời lớp): Tắt SSID 'teacher_5g' để giải phóng Airtime.
    """
    if target_ip in PRIVILEGED_OFFICE_IPS:
        return

    logging.info(f"[🔍] Kiểm tra trạng thái sử dụng mạng '{FAKE_SSID}' tại AP {target_ip} trước khi tỉa SSID...")
    pruned = actuators.prune_unused_ssid(target_ip, USER, PWD, vap_section="teacher_5g", disable=True)

    if pruned:
        ap_status[target_ip]["is_woken"] = False
        ap_status[target_ip]["timer"] = None
        logging.info(f"[✂️] Giáo viên đã rời khu vực. Đã TẮT '{FAKE_SSID}' trên AP {target_ip} để tiết kiệm Airtime!")
    else:
        # Vẫn còn Giáo viên đang kết nối -> Tự động gia hạn đếm ngược!
        timer = threading.Timer(WAKE_DURATION, try_turn_off_teacher_network, args=[target_ip])
        timer.daemon = True
        timer.start()
        ap_status[target_ip]["timer"] = timer


def wake_up_router(client_mac, target_ip):
    if not target_ip or target_ip not in ap_status:
        return
    if target_ip in PRIVILEGED_OFFICE_IPS:
        return  # Phòng GV đã luôn bật sẵn

    status = ap_status[target_ip]
    if not status["is_woken"]:
        logging.info(f"🚀 PHÁT HIỆN THIẾT BỊ GIÁO VIÊN ({client_mac}) GẦN AP {target_ip}! Đang bật '{FAKE_SSID}'...")
        status["is_woken"] = True
        actuators.prune_unused_ssid(target_ip, USER, PWD, vap_section="teacher_5g", disable=False)

    # Đặt hoặc làm mới bộ đếm kiểm tra rời vùng phủ sóng
    if status["timer"]:
        status["timer"].cancel()
    timer = threading.Timer(WAKE_DURATION, try_turn_off_teacher_network, args=[target_ip])
    timer.daemon = True
    timer.start()
    status["timer"] = timer


def process_packet(packet):
    if packet.haslayer(Dot11ProbeReq) or packet.haslayer(Dot11Auth) or packet.haslayer(Dot11AssoReq):
        client_mac = packet.addr2
        bssid_receiver = packet.addr1

        if client_mac:
            client_mac = client_mac.upper()
            vip_macs = get_vip_macs()

            if client_mac in vip_macs:
                if bssid_receiver and bssid_receiver.upper() in AP_MAP:
                    target_ip = AP_MAP[bssid_receiver.upper()]
                    threading.Thread(target=wake_up_router, args=(client_mac, target_ip), daemon=True).start()
                else:
                    # Nếu điện thoại GV phát Probe Request dạng Broadcast (FF:FF:FF:FF:FF:FF),
                    # đánh thức các AP phòng học trong vùng phủ sóng
                    for ip, st in ap_status.items():
                        if st["zone_type"] == "classroom":
                            threading.Thread(target=wake_up_router, args=(client_mac, ip), daemon=True).start()


def active_presence_monitor_loop():
    """
    Luồng phụ trợ chạy song song (kể cả khi chưa cắm USB Wi-Fi):
    Định kỳ mỗi 30 giây kiểm tra các AP phòng học đang bật mạng Teacher,
    nếu không có timer (do được bật trực tiếp từ trang Onboarding app.py) thì gắn timer tự động tỉa khi GV rời đi!
    """
    while True:
        time.sleep(30)
        for ip, st in ap_status.items():
            if st["zone_type"] == "classroom" and st["timer"] is None:
                active_macs = actuators.get_teacher_active_clients(ip, USER, PWD, FAKE_SSID)
                if len(active_macs) > 0:
                    st["is_woken"] = True
                    timer = threading.Timer(WAKE_DURATION, try_turn_off_teacher_network, args=[ip])
                    timer.daemon = True
                    timer.start()
                    st["timer"] = timer


if __name__ == "__main__":
    if not ap_status:
        logging.critical("[-] File network_config.yaml trống!")
        exit(1)

    threading.Thread(target=active_presence_monitor_loop, daemon=True).start()
    threading.Thread(target=inject_fake_beacon, daemon=True).start()

    logging.info(f"[*] Vệ sĩ Mồi nhử & Safe SSID Pruning đang lắng nghe trên {IFACE}...")
    try:
        sniff(iface=IFACE, prn=process_packet, store=0)
    except Exception as e:
        logging.warning(f"[!] Chế độ Sniff trên {IFACE} tạm ngưng ({e}). Đang chạy ở chế độ SSH Presence Monitor...")
        while True:
            time.sleep(5)