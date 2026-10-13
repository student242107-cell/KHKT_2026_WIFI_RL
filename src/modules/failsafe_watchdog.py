import time
import requests
import paramiko
import subprocess
import os
import logging
import yaml
from dotenv import load_dotenv
from netmiko import ConnectHandler

# Thư viện quản trị thiết bị mạng chuẩn Doanh nghiệp
try:
    from netmiko import ConnectHandler
except ImportError:
    logging.warning("Chưa cài netmiko. Chạy 'pip install netmiko' để dùng tính năng PoE Switch Hard-Reboot.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [WATCHDOG] - %(levelname)s - %(message)s')

# =====================================================================
# 1. NẠP CẤU HÌNH HỆ THỐNG & TÔPÔ MẠNG
# ========================================== ===========================
load_dotenv()
ROUTER_USER = os.getenv("ROUTER_USER", "root")
ROUTER_PASS = os.getenv("ROUTER_PASS")

# Chế độ cứu hộ cứng: 'SONOFF' (Cho phòng Lab KHKT) hoặc 'POE_SWITCH' (Thực tế trường học)
HARD_FAILSAFE_MODE = os.getenv("HARD_FAILSAFE_MODE", "SONOFF")

# --- Cấu hình thiết bị ngoại vi ---
SONOFF_IP = os.getenv("SONOFF_IP", "192.168.137.2")
SWITCH_IP = os.getenv("SWITCH_IP", "192.168.1.254")
SWITCH_USER = os.getenv("SWITCH_USER", "admin")
SWITCH_PASS = os.getenv("SWITCH_PASS", "admin123")
SWITCH_TYPE = os.getenv("SWITCH_TYPE", "cisco_ios")

CONFIG_PATH = "configs/network_config.yaml"
FAILSAFE_LOG_PATH = "data/processed/failsafe_events.log" # Log để AI đọc và áp dụng mức phạt Lambda_fail = 500

def load_ap_map():
    """Tự động tải danh sách AP từ Cấu trúc liên kết mạng (Network Topology)"""
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
            return config.get('network_topology', {}).get('ap_map', {})
    except Exception as e:
        logging.error(f"Lỗi đọc file YAML: {e}")
        return {}

# Từ điển quản lý rủi ro cho từng AP
# Cấu trúc: { "192.168.1.2": {"consecutive_fails": 0, "poe_port": "Gi1/0/2"} }
ap_health_monitor = {}
AP_MAP = load_ap_map()

for mac, ip in AP_MAP.items():
    # Giả định gán cổng PoE dồn từ Gi1/0/1 trở đi nếu triển khai thực tế.
    # Trong file YAML có thể thiết kế thêm mục PoE_Port cho từng MAC.
    ap_health_monitor[ip] = {"consecutive_fails": 0, "poe_port": "Gi1/0/1"}

# =====================================================================
# 2. CÁC HÀM TIỆN ÍCH GIÁM SÁT (SENSING) & GHI NHẬN HÌNH PHẠT
# =====================================================================

def log_failsafe_event(ap_ip, level):
    """Ghi nhận sự kiện sập mạng để AI PPO đọc và áp dụng Penalty M>>N"""
    try:
        os.makedirs(os.path.dirname(FAILSAFE_LOG_PATH), exist_ok=True)
        with open(FAILSAFE_LOG_PATH, 'a') as f:
            f.write(f"{time.time()},{ap_ip},{level}\n")
    except Exception:
        pass

import subprocess
import logging

def check_ap_latency(ap_ip):
    """
    Đo độ trễ mạng (Latency/RTT) chuẩn xác bằng kỹ thuật Multi-packet Burst.
    Loại bỏ cảnh báo giả (False Positive) do rơi gói tin ngẫu nhiên.
    """
    try:
        # Gửi 3 gói tin (-c 3) với timeout 2 giây (-W 2)
        # Sử dụng tùy chọn -q (quiet) để chỉ lấy phần thống kê cuối cùng nhằm giảm tải CPU xử lý chuỗi
        cmd = f"ping -c 3 -W 2 -q {ap_ip}"
        output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8')
        
        # Trích xuất Tỷ lệ mất gói (Packet Loss)
        # Ví dụ output: "3 packets transmitted, 3 received, 0% packet loss"
        if "packet loss" in output:
            loss_str = output.split("packet loss")[0].split(",")[-1].strip().replace("%", "")
            packet_loss = float(loss_str)
            
            # Nếu mất gói 100% -> Chắc chắn sập mạng
            if packet_loss == 100.0:
                return 9999.0
        
        # Trích xuất Độ trễ trung bình (Avg Latency)
        # Ví dụ output: "rtt min/avg/max/mdev = 1.234/5.678/9.012/1.111 ms"
        if "min/avg/max" in output:
            # Lấy chuỗi chứa các con số: "1.234/5.678/9.012/1.111"
            rtt_stats = output.split("=")[1].split("ms")[0].strip()
            # Cắt theo dấu "/" và lấy phần tử thứ 2 (chính là avg)
            avg_latency = float(rtt_stats.split("/")[1])
            return avg_latency
            
    except subprocess.CalledProcessError:
        # Lỗi sinh ra khi ping thất bại hoàn toàn (Host unreachable)
        return 9999.0
    except Exception as e:
        logging.error(f"[!] Lỗi phân tích Latency trên AP {ap_ip}: {e}")
        
    return 9999.0 # Trả về ngưỡng nguy kịch nếu có lỗi không xác định

# =====================================================================
# 3. CƠ CHẾ DUAL FAIL-SAFE (SOFT & HARD RESCUE)
# =====================================================================

def soft_reset_ap(ap_ip):
    """CỨU HỘ MỀM: Ghi đè cấu hình lỗi, khôi phục Safe-fallback Profile"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        logging.info(f"[SOFT-RESCUE] Đang SSH vào AP {ap_ip} để khôi phục cấu hình an toàn...")
        ssh.connect(ap_ip, username=ROUTER_USER, password=ROUTER_PASS, timeout=5)
        # Fallback về 20MHz, Auto Channel, Auto Power để đảm bảo mạng sống lại
        rescue_cmd = (
            "uci set wireless.radio0.channel='auto' && "
            "uci set wireless.radio0.htmode='HT20' && "
            "uci set wireless.radio0.txpower='auto' && "
            "uci commit wireless && wifi reload"
        )
        ssh.exec_command(rescue_cmd)
        log_failsafe_event(ap_ip, level="SOFT")
        return True
    except Exception as e:
        logging.error(f"[SOFT-RESCUE FAILED] AP {ap_ip} đã treo cứng (Kernel Panic): {e}")
        return False
    finally:
        ssh.close()

def hard_reboot_ap(ap_ip, poe_port):
    """CỨU HỘ CỨNG: Ngắt điện vật lý khi Soft-Rescue bất lực"""
    log_failsafe_event(ap_ip, level="HARD")
    
    if HARD_FAILSAFE_MODE == "SONOFF":
        logging.info(f"[HARD-RESCUE] Kích hoạt Sonoff Plug cắt điện 220V cho AP {ap_ip}...")
        try:
            payload = {"data": {"switch": "off", "pulse": "on", "pulseWidth": 5000}} # Ngắt 5s
            requests.post(f"http://{SONOFF_IP}/zeroconf/switch", json=payload, timeout=3)
            return True
        except Exception as e:
            logging.critical(f"[-] Lỗi Sonoff: {e}")
            return False
            
    elif HARD_FAILSAFE_MODE == "POE_SWITCH":
        logging.info(f"[HARD-RESCUE] Đăng nhập PoE Switch {SWITCH_IP}, Cắt điện cổng {poe_port}...")
        try:
            net_connect = ConnectHandler(device_type=SWITCH_TYPE, ip=SWITCH_IP, username=SWITCH_USER, password=SWITCH_PASS)
            net_connect.enable()
            # Ngắt nguồn PoE
            net_connect.send_config_set([f"interface {poe_port}", "power inline never"])
            time.sleep(5)
            # Cấp lại nguồn PoE
            net_connect.send_config_set([f"interface {poe_port}", "power inline auto"])
            net_connect.disconnect()
            return True
        except Exception as e:
            logging.critical(f"[-] Lỗi PoE Switch: {e}")
            return False

# =====================================================================
# 4. VÒNG LẶP ĐIỀU PHỐI (ORCHESTRATOR LOOP)
# =====================================================================

def main():
    print("==================================================================")
    print(" 🛡️ COGNITIVE WI-FI: DUAL FAIL-SAFE WATCHDOG DAEMON 🛡️")
    print(f" [*] Đang giám sát {len(AP_MAP)} Access Points. Hard-Mode: {HARD_FAILSAFE_MODE}")
    print("==================================================================")
    
    if not AP_MAP:
        logging.critical("Chưa có cấu trúc mạng trong YAML. Thoát Daemon.")
        return

    while True:
        try:
            # Khám sức khỏe từng AP (Per-AP Monitoring)
            for mac, ap_ip in AP_MAP.items():
                latency = check_ap_latency(ap_ip)
                
                # Nếu Ping > 500ms hoặc mất mạng hoàn toàn (9999ms)
                if latency > 500.0:
                    ap_health_monitor[ap_ip]["consecutive_fails"] += 1
                    fails = ap_health_monitor[ap_ip]["consecutive_fails"]
                    logging.warning(f"⚠️ AP {ap_ip} đang nghẽn/sập. Latency: {latency}ms ({fails}/6)")
                    
                    # Nếu 6 lần liên tiếp (30 giây) mạng không phản hồi -> KÍCH HOẠT DUAL FAIL-SAFE
                    if fails >= 6:
                        logging.critical(f"🚨 AP {ap_ip} MẤT KIỂM SOÁT! Kích hoạt quy trình giải cứu...")
                        
                        # Bước 1: Cứu hộ phần mềm (Soft Rescue)
                        if not soft_reset_ap(ap_ip):
                            # Bước 2: Cứu hộ phần cứng (Hard Rescue)
                            poe_port = ap_health_monitor[ap_ip]["poe_port"]
                            hard_reboot_ap(ap_ip, poe_port)
                            
                        # Sau khi cứu hộ, reset bộ đếm và cho AP này thời gian khởi động (60s)
                        ap_health_monitor[ap_ip]["consecutive_fails"] = 0
                        logging.info(f"⏳ Hệ thống AP {ap_ip} đang khôi phục. Bỏ qua giám sát AP này trong 60s...")
                        time.sleep(60) # Chỉ sleep tạm, thực tế có thể dùng timer bất đồng bộ
                else:
                    if ap_health_monitor[ap_ip]["consecutive_fails"] > 0:
                        logging.info(f"✅ AP {ap_ip} đã ổn định trở lại ({latency}ms).")
                    ap_health_monitor[ap_ip]["consecutive_fails"] = 0
            
            # Cửa sổ quét sức khỏe định kỳ (5 giây 1 vòng quét toàn mạng)
            time.sleep(5)
            
        except KeyboardInterrupt:
            print("\n[+] Đã tắt hệ thống Watchdog.")
            break
        except Exception as e:
            logging.error(f"Lỗi Daemon: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()