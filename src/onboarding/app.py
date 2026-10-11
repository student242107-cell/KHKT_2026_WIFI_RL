from flask import Flask, render_template_string, request, redirect
import json
import os
import re

app = Flask(__name__)

VIP_MAC_FILE = "data/processed/mac_vip_list.json"

# Cấu hình danh sách Giáo viên (Hardcode tạm cho đơn giản, hoặc đọc từ file Excel của trường)
TEACHER_DB = {
    "GV001": "123456",
    "GV002": "password"
}

# Đảm bảo file JSON tồn tại
if not os.path.exists(VIP_MAC_FILE):
    os.makedirs(os.path.dirname(VIP_MAC_FILE), exist_ok=True)
    with open(VIP_MAC_FILE, 'w') as f:
        json.dump([], f)

# TEMPLATE HTML (Open-source, dễ custom)
# IT trường chỉ cần sửa lại đoạn HTML này là đổi được giao diện theo màu của trường.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Cổng Xác Thực Giáo Viên</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f4f9; display: flex; justify-content: center; align-items: center; height: 100vh; }
        .login-box { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.2); text-align: center; }
        input { margin: 10px 0; padding: 10px; width: 90%; border: 1px solid #ccc; border-radius: 5px; }
        button { padding: 10px 20px; background-color: #0056b3; color: white; border: none; border-radius: 5px; cursor: pointer; }
        .error { color: red; font-size: 14px; }
    </style>
</head>
<body>
    <div class="login-box">
        <h2>HỆ THỐNG MẠNG THÔNG MINH KHKT</h2>
        <p>Vui lòng đăng nhập để đăng ký thiết bị (Thiết bị của bạn sẽ tự động được ghi nhận MAC)</p>
        {% if error %}<p class="error">{{ error }}</p>{% endif %}
        <form method="POST">
            <input type="text" name="gv_id" placeholder="Mã số Giáo viên (VD: GV001)" required><br>
            <input type="password" name="password" placeholder="Mật khẩu" required><br>
            <button type="submit">Xác Thực & Đăng Ký Thiết Bị</button>
        </form>
    </div>
</body>
</html>
"""

SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html>
<body>
    <div style="text-align: center; margin-top: 20%;">
        <h2 style="color: green;">✅ XÁC THỰC THÀNH CÔNG!</h2>
        <p>Địa chỉ MAC của thiết bị này đã được AI ghi nhận vào danh sách VIP.</p>
        <p>Mật khẩu mạng nội bộ (School_Teacher_Dynamic) của bạn là: <b>TeacherPassword456</b></p>
        <p>Vui lòng vào cài đặt Wi-Fi và kết nối vào mạng trên. Mạng sẽ tự động bật khi bạn đến gần.</p>
    </div>
</body>
</html>
"""

def get_client_mac(ip):
    """Hàm trích xuất MAC từ ARP table của máy chủ dựa trên IP gửi Request"""
    try:
        # Chạy lệnh arp -n để tìm MAC tương ứng với IP
        pid = os.popen(f"arp -n {ip} | awk '/^[0-9]/ {{print $3}}'")
        mac = pid.read().strip()
        # Kiểm tra đúng định dạng MAC
        if re.match(r"([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})", mac):
            return mac.upper()
    except Exception:
        pass
    return None

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        gv_id = request.form.get('gv_id')
        password = request.form.get('password')
        
        # 1. Xác thực tài khoản
        if gv_id in TEACHER_DB and TEACHER_DB[gv_id] == password:
            # 2. Lấy IP của điện thoại đang truy cập
            client_ip = request.remote_addr
            
            # 3. Tra bảng ARP để lấy MAC từ IP đó
            client_mac = get_client_mac(client_ip)
            
            if client_mac:
                # 4. Lưu vào JSON
                with open(VIP_MAC_FILE, 'r') as f:
                    vip_list = json.load(f)
                
                if client_mac not in vip_list:
                    vip_list.append(client_mac)
                    with open(VIP_MAC_FILE, 'w') as f:
                        json.dump(vip_list, f)
                        
                return render_template_string(SUCCESS_TEMPLATE)
            else:
                return render_template_string(HTML_TEMPLATE, error="Không thể nhận diện MAC thiết bị. Vui lòng tắt 4G/LTE và thử lại.")
        else:
            return render_template_string(HTML_TEMPLATE, error="Sai Mã số GV hoặc Mật khẩu!")
            
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    # Chạy Web Server ở cổng 80 (Cổng mặc định HTTP) để điện thoại dễ tự động bật Captive Portal
    app.run(host='0.0.0.0', port=80)