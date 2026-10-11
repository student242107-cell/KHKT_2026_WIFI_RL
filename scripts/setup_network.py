import paramiko
import yaml
import os
from dotenv import load_dotenv

CONFIG_PATH = "configs/network_config.yaml"
load_dotenv() 

USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")

def load_ap_map():
    """Tự động tải danh sách IP của tất cả AP từ file YAML"""
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
            return config.get('network_topology', {}).get('ap_map', {})
    except Exception as e:
        print(f"[-] Lỗi đọc file YAML: {e}")
        return {}

def setup_multiple_bssid(ip, username, password):
    """Gửi lệnh UCI setup 2 SSID trên băng tần 5GHz (radio0)"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"[*] Đang kết nối SSH tới AP {ip} để tạo Vùng Đặc Quyền...")
        ssh.connect(ip, username=username, password=password, timeout=10)
        
        # Xóa cấu hình cũ và định nghĩa Radio0 (Băng tần 5GHz)
        command = (
            "uci set wireless.default_radio0=wifi-iface && "
            "uci set wireless.default_radio0.device='radio0' && "
            "uci set wireless.default_radio0.network='lan' && "
            "uci set wireless.default_radio0.mode='ap' && "
            "uci set wireless.default_radio0.ssid='School_Student' && "
            "uci set wireless.default_radio0.encryption='psk2' && "
            "uci set wireless.default_radio0.key='StudentPassword123' && "
            
            "uci set wireless.teacher_radio0=wifi-iface && "
            "uci set wireless.teacher_radio0.device='radio0' && "
            "uci set wireless.teacher_radio0.network='lan' && "
            "uci set wireless.teacher_radio0.mode='ap' && "
            "uci set wireless.teacher_radio0.ssid='School_Teacher_Dynamic' && "
            "uci set wireless.teacher_radio0.encryption='psk2' && "
            "uci set wireless.teacher_radio0.key='TeacherPassword456' && "
            
            "uci commit wireless && wifi reload"
        )
        
        ssh.exec_command(command)
        print(f"[+] AP {ip}: Đã tạo xong 2 SSID (Student & Dynamic Teacher).")
    except Exception as e:
        print(f"[-] Lỗi SSH trên AP {ip}: {e}")
    finally:
        ssh.close()

if __name__ == "__main__":
    print("=== TIẾN TRÌNH KHỞI TẠO MẠNG VÔ TUYẾN ===")
    ap_map = load_ap_map()
    if not ap_map:
        print("[-] Không có AP nào trong danh sách. Hãy chạy auto_discover_aps.py trước.")
        exit()
        
    for mac, ip in ap_map.items():
        # Setup đồng loạt trên tất cả các AP trong mạng
        setup_multiple_bssid(ip, USER, PWD)
    
    print("\n✅ HOÀN TẤT KHỞI TẠO! Mạng đã sẵn sàng đón luồng AI.")