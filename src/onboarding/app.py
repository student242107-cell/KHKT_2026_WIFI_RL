from flask import Flask, render_template_string, request
import json
import os
import re
import paramiko
import threading
import time

app = Flask(__name__)

# --- CẤU HÌNH ---
VIP_MAC_FILE = "data/processed/mac_vip_list.json"
TEACHER_DB_FILE = "configs/teacher_db.json"

ROUTER_IP = os.getenv("ROUTER_IP", "192.168.1.1")
ROUTER_USER = os.getenv("ROUTER_USER", "root")
ROUTER_PASS = os.getenv("ROUTER_PASS")
# Đặt tên Interface của mạng Onboarding (VD: wlan0-1 hoặc wlan1-1 tùy lúc setup)
ONBOARDING_IFACE = "wlan0-1" 

if not os.path.exists(VIP_MAC_FILE):
    os.makedirs(os.path.dirname(VIP_MAC_FILE), exist_ok=True)
    with open(VIP_MAC_FILE, 'w') as f: json.dump([], f)

if not os.path.exists(TEACHER_DB_FILE):
    os.makedirs(os.path.dirname(TEACHER_DB_FILE), exist_ok=True)
    with open(TEACHER_DB_FILE, 'w') as f: json.dump({"GV001": "pass123"}, f)

# --- GIAO DIỆN HTML ---
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cổng Xác Thực</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, sans-serif; background-color: #f4f7f6; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .card { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 8px 16px rgba(0,0,0,0.1); text-align: center; max-width: 400px; width: 90%; }
        h2 { color: #333; margin-bottom: 10px; }
        p { color: #666; font-size: 14px; margin-bottom: 25px; }
        input { margin: 10px 0; padding: 12px; width: 90%; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; font-size: 16px; }
        button { margin-top: 15px; padding: 12px; width: 100%; background-color: #0056b3; color: white; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; }
        .error { color: #dc3545; font-size: 14px; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>MẠNG KHKT 2026</h2>
        <p>Vui lòng đăng nhập để hệ thống tự động đăng ký thiết bị (Zero-Touch).</p>
        <form method="POST" action="/">
            <input type="text" name="gv_id" placeholder="Mã số Giáo viên (VD: GV001)" required>
            <input type="password" name="password" placeholder="Mật khẩu" required>
            <button type="submit">Xác Thực & Đăng Ký</button>
        </form>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
    </div>
</body>
</html>
"""

SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Thành Công</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #e8f5e9; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 8px 16px rgba(0,0,0,0.1); text-align: center; max-width: 400px; width: 90%; border-top: 5px solid #4CAF50;}
        h2 { color: #4CAF50; }
        p { line-height: 1.6; color: #333;}
        .highlight { background-color: #f1f1f1; padding: 10px; border-radius: 5px; font-weight: bold; font-size: 18px; letter-spacing: 1px;}
    </style>
</head>
<body>
    <div class="card">
        <h2>✅ ĐĂNG KÝ THÀNH CÔNG!</h2>
        <p>Hệ thống AI đã ghi nhận MAC thiết bị của Thầy/Cô.</p>
        <p>Mật khẩu mạng <b>School_Teacher</b> là:</p>
        <div class="highlight">TeacherPassword456</div>
        <p style="font-size: 13px; color: #666; margin-top: 20px;">* Đang ngắt kết nối mạng tạm thời... Vui lòng mở Cài đặt Wi-Fi và chọn mạng School_Teacher.</p>
    </div>
</body>
</html>
"""

# --- CÁC HÀM CÔNG CỤ KỸ THUẬT MẠNG ---

def get_client_mac(ip):
    """Tra cứu bảng ARP Layer 2 để lấy MAC từ IP"""
    try:
        pid = os.popen(f"arp -n {ip} | awk '/^[0-9]/ {{print $3}}'")
        mac = pid.read().strip()
        if re.match(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", mac):
            return mac.upper()
    except Exception:
        pass
    return None

def kick_device_from_onboarding(mac_address):
    """MA THUẬT: Dùng SSH gửi lệnh đá thiết bị khỏi mạng Onboarding"""
    try:
        print(f"[*] Đang thực thi lệnh Deauth để đá thiết bị {mac_address} ra khỏi mạng...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ROUTER_IP, username=ROUTER_USER, password=ROUTER_PASS, timeout=5)
        # Lệnh xóa thiết bị khỏi trạm AP trên OpenWrt
        ssh.exec_command(f"iw dev {ONBOARDING_IFACE} station del {mac_address}")
        ssh.close()
        print(f"[+] Đã đá văng thành công. Thiết bị sẽ tự động dò tìm mạng School_Teacher.")
    except Exception as e:
        print(f"[-] Lỗi khi kick thiết bị: {e}")

# --- BỘ LỌC CATCH-ALL ĐỂ TẠO HIỆU ỨNG CAPTIVE PORTAL ---
# Lệnh bọc này đảm bảo ĐIỆN THOẠI HỎI TRANG NÀO CŨNG BỊ TRẢ VỀ ĐÂY
@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def captive_portal(path):
    if request.method == 'POST':
        gv_id = request.form.get('gv_id')
        password = request.form.get('password')
        
        with open(TEACHER_DB_FILE, 'r') as f:
            teacher_db = json.load(f)
        
        if gv_id in teacher_db and teacher_db[gv_id] == password:
            client_ip = request.remote_addr
            client_mac = get_client_mac(client_ip)
            
            if client_mac:
                with open(VIP_MAC_FILE, 'r') as f:
                    vip_list = json.load(f)
                
                if client_mac not in vip_list:
                    vip_list.append(client_mac)
                    with open(VIP_MAC_FILE, 'w') as f:
                        json.dump(vip_list, f)
                        
                print(f"[+] Đã cấp quyền VIP cho: {client_mac} (GV: {gv_id})")
                
                # CHỜ 3 GIÂY ĐỂ TRÌNH DUYỆT TẢI XONG TRANG SUCCESS, RỒI MỚI ĐÁ VĂNG!
                threading.Timer(3.0, kick_device_from_onboarding, args=[client_mac]).start()
                
                return render_template_string(SUCCESS_TEMPLATE)
            else:
                return render_template_string(LOGIN_TEMPLATE, error="Không thể nhận diện MAC thiết bị.")
        else:
            return render_template_string(LOGIN_TEMPLATE, error="Sai Mã số GV hoặc Mật khẩu!")
            
    # Trả về trang Login cho các request Check Internet của OS
    return render_template_string(LOGIN_TEMPLATE)

if __name__ == '__main__':
    print("[*] Cổng Captive Portal đang chạy ở cổng 80 (Bắt mọi request)...")
    app.run(host='0.0.0.0', port=80)