import time
import requests
import paramiko
import subprocess
import os
import logging
from dotenv import load_dotenv
from netmiko import ConnectHandler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 1. CẤU HÌNH MÔI TRƯỜNG & THIẾT BỊ ---
load_dotenv()
ROUTER_IP = os.getenv("ROUTER_IP", "192.168.1.1")
ROUTER_USER = os.getenv("ROUTER_USER", "root")
ROUTER_PASS = os.getenv("ROUTER_PASS")

# Xác định môi trường chạy (Testbed dùng Sonoff, Thực tế dùng PoE Switch)
HARD_FAILSAFE_MODE = os.getenv("HARD_FAILSAFE_MODE", "SONOFF") # Có thể là 'SONOFF' hoặc 'POE_SWITCH'

# Cấu hình Sonoff S26 R2 (Môi trường R&D)
SONOFF_IP = os.getenv("SONOFF_IP", "192.168.137.2") # Nằm ở mạng cách ly Mobile Hotspot

# Cấu hình Managed PoE Switch (Môi trường Thực tế)
SWITCH_IP = os.getenv("SWITCH_IP", "192.168.1.254")
SWITCH_USER = os.getenv("SWITCH_USER", "admin")
SWITCH_PASS = os.getenv("SWITCH_PASS", "admin123")
SWITCH_TYPE = os.getenv("SWITCH_TYPE", "cisco_ios") # Hỗ trợ cisco_ios, hp_procurve, tp_link...
ROUTER_POE_PORT = os.getenv("ROUTER_POE_PORT", "Gi1/0/1") # Cổng mạng đang cắm Router


# --- 2. CÁC HÀM TIỆN ÍCH GIÁM SÁT ---

def check_ping(ip):
    """Kiểm tra độ trễ mạng (Ping)"""
    try:
        output = subprocess.check_output(f"ping -c 1 -W 2 {ip}", shell=True, timeout=3).decode('utf-8')
        if "time=" in output:
            return float(output.split('time=')[1].split(' ms')[0])
    except Exception:
        pass
    return 9999.0 # Lỗi mất kết nối

def soft_reset_router():
    """Cứu hộ Cấp 1: SSH vào Router và ép reload lại cấu hình gốc"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        logging.info(f"[*] Đang thử SSH Soft-Reset vào Router {ROUTER_IP}...")
        ssh.connect(ROUTER_IP, username=ROUTER_USER, password=ROUTER_PASS, timeout=5)
        # Khôi phục Router về Auto-Channel và khởi động lại Wi-Fi
        ssh.exec_command("uci set wireless.radio0.channel='auto' && uci commit wireless && wifi reload")
        return True
    except Exception as e:
        logging.error(f"[-] Soft-Reset thất bại (Có thể do tràn RAM OOM): {e}")
        return False
    finally:
        ssh.close()


# --- 3. CÁC HÀM CỨU HỘ VẬT LÝ (HARD REBOOT) ---

def hard_reboot_sonoff():
    """Cứu hộ Cấp 2 (R&D Mode): Cắt điện 220V qua Local REST API của Ổ cắm thông minh."""
    try:
        logging.info(f"[*] Đang kích hoạt Hard-Reboot qua Sonoff Plug ({SONOFF_IP})...")
        payload = {"data": {"switch": "off", "pulse": "on", "pulseWidth": 5000}} # Tắt 5s rồi bật lại
        requests.post(f"http://{SONOFF_IP}/zeroconf/switch", json=payload, timeout=3)
        logging.info("[+] Thành công: Đã chớp điện 220V!")
        return True
    except Exception as e:
        logging.critical(f"[-] Lỗi kết nối ổ cắm Sonoff: {e}")
        return False

def hard_reboot_poe_switch():
    """
    Cứu hộ Cấp 2 (Production Mode): SSH vào Managed Switch và ngắt/bật lại cổng PoE.
    Giả lập rút cáp mạng cấp nguồn cho Router trên thực địa.
    """
    try:
        logging.info(f"[*] Đang kích hoạt Hard-Reboot qua PoE Switch ({SWITCH_IP} - Cổng {ROUTER_POE_PORT})...")
        
        switch_device = {
            'device_type': SWITCH_TYPE,
            'ip': SWITCH_IP,
            'username': SWITCH_USER,
            'password': SWITCH_PASS,
            'port': 22,
        }
        
        # Kết nối vào Switch
        net_connect = ConnectHandler(**switch_device)
        net_connect.enable()
        
        # Chuỗi lệnh ngắt và bật lại nguồn PoE (Ví dụ theo chuẩn Cisco IOS)
        commands = [
            f"interface {ROUTER_POE_PORT}",
            "power inline never", # Tắt cấp nguồn PoE
        ]
        net_connect.send_config_set(commands)
        logging.info("   [+] Đã ngắt nguồn PoE. Chờ 5 giây...")
        time.sleep(5)
        
        # Bật lại nguồn
        commands_on = [
            f"interface {ROUTER_POE_PORT}",
            "power inline auto" # Bật lại cấp nguồn PoE
        ]
        net_connect.send_config_set(commands_on)
        logging.info("   [+] Đã cấp lại nguồn PoE. Router đang khởi động lại.")
        
        net_connect.disconnect()
        return True
        
    except Exception as e:
        logging.critical(f"[-] Lỗi điều khiển PoE Switch: {e}")
        return False

def trigger_hard_reboot():
    """Hàm định tuyến quyết định dùng thiết bị phần cứng nào để cắt điện"""
    if HARD_FAILSAFE_MODE == "POE_SWITCH":
        hard_reboot_poe_switch()
    else:
        hard_reboot_sonoff()


# --- 4. VÒNG LẶP WATCHDOG GIÁM SÁT ---

def main():
    print("=========================================================")
    print(" 🛡️ HỆ THỐNG PHÒNG THỦ KÉP (DUAL FAIL-SAFE WATCHDOG) 🛡️")
    print(f" Chế độ Cứu hộ Phần cứng (Hard Reboot): {HARD_FAILSAFE_MODE}")
    print("=========================================================")
    
    consecutive_high_ping = 0
    
    while True:
        try:
            current_ping = check_ping(ROUTER_IP)
            
            if current_ping > 500: # Ngưỡng nguy kịch
                consecutive_high_ping += 1
                logging.warning(f"Ping cao {current_ping}ms ({consecutive_high_ping}/6)")
            else:
                if consecutive_high_ping > 0:
                    logging.info(f"Mạng ổn định trở lại ({current_ping}ms). Reset bộ đếm.")
                consecutive_high_ping = 0 
                
            # Nếu 6 lần test liên tiếp (khoảng 30 giây) mạng bị sập
            if consecutive_high_ping >= 6:
                logging.critical("HỆ THỐNG NGUY KỊCH! Đang kích hoạt Soft-Reset...")
                
                # Thử cứu hộ bằng phần mềm trước
                if not soft_reset_router():
                    logging.critical("Cứu hộ phần mềm thất bại. Đang kích hoạt Hard-Reboot Vật lý!")
                    trigger_hard_reboot() # Cắt điện vật lý
                    
                consecutive_high_ping = 0
                logging.info("Hệ thống đang phục hồi. Tạm dừng giám sát 60 giây...")
                time.sleep(60) # Chờ Router khởi động xong mới giám sát tiếp
                
            time.sleep(5)
            
        except KeyboardInterrupt:
            print("\n[+] Đã dừng Vệ sĩ giám sát.")
            break
        except Exception as e:
            logging.error(f"Lỗi Vòng Lặp Watchdog: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()