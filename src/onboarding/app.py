from flask import Flask, render_template_string, request
import json
import os
import re
import paramiko
import threading
import yaml
from dotenv import load_dotenv

import src.modules.wifi_actuators as actuators

load_dotenv()

app = Flask(__name__)

# --- CẤU HÌNH ---
VIP_MAC_FILE = "data/processed/mac_vip_list.json"
TEACHER_DB_FILE = "configs/teacher_db.json"
CONFIG_PATH = "configs/network_config.yaml"

ROUTER_IP = os.getenv("ROUTER_IP", "192.168.2.1")
ROUTER_USER = os.getenv("ROUTER_USER", "root")
ROUTER_PASS = os.getenv("ROUTER_PASS", "")
TEACHER_SSID = os.getenv("TEACHER_SSID", "School_Teacher")
TEACHER_PASS = os.getenv("TEACHER_PASSWORD", "TeacherPassword456")
ONBOARD_SSID = os.getenv("ONBOARD_SSID", "School_Onboarding")

if not os.path.exists(VIP_MAC_FILE):
    os.makedirs(os.path.dirname(VIP_MAC_FILE), exist_ok=True)
    with open(VIP_MAC_FILE, 'w') as f:
        json.dump([], f)

if not os.path.exists(TEACHER_DB_FILE):
    os.makedirs(os.path.dirname(TEACHER_DB_FILE), exist_ok=True)
    with open(TEACHER_DB_FILE, 'w') as f:
        json.dump({"GV001": "pass123", "GV002": "pass123"}, f, indent=2)

# --- GIAO DIỆN HTML ---
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cổng Xác Thực Giáo Viên - Cognitive Wi-Fi</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, sans-serif; background: linear-gradient(135deg, #e0eafc 0%, #cfdef3 100%); display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .card { background: white; padding: 36px 28px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.12); text-align: center; max-width: 400px; width: 90%; border-top: 6px solid #0056b3; }
        h2 { color: #1a2a3a; margin-bottom: 8px; font-size: 22px; }
        p { color: #555; font-size: 14px; margin-bottom: 22px; line-height: 1.5; }
        input { margin: 8px 0; padding: 13px; width: 100%; border: 1px solid #ccd6e0; border-radius: 8px; box-sizing: border-box; font-size: 16px; }
        button { margin-top: 16px; padding: 14px; width: 100%; background-color: #0056b3; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; transition: 0.2s; }
        button:active { transform: scale(0.98); background-color: #003d82; }
        .error { color: #dc3545; font-size: 14px; margin-top: 14px; font-weight: 600; }
    </style>
</head>
<body>
    <div class="card">
        <h2>📡 CỔNG ĐỊNH DANH GIÁO VIÊN</h2>
        <p>Hệ thống Cognitive Wi-Fi — Tự động kích hoạt vùng sóng ưu tiên <b>{{ teacher_ssid }}</b> (Zero-Touch Onboarding).</p>
        <form method="POST" action="/">
            <input type="text" name="gv_id" placeholder="Mã số Giáo viên (VD: GV001)" required>
            <input type="password" name="password" placeholder="Mật khẩu xác thực" required>
            <button type="submit">🔐 Xác Thực & Mở Sóng VIP</button>
        </form>
        {% if error %}<div class="error">⚠️ {{ error }}</div>{% endif %}
    </div>
</body>
</html>
"""

SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Đăng Ký Thành Công</title>
    <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; background-color: #e8f5e9; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .card { background: white; padding: 30px 24px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.12); text-align: center; max-width: 410px; width: 90%; border-top: 6px solid #2e7d32; }
        h2 { color: #2e7d32; margin-top: 0; }
        p { line-height: 1.5; color: #333; font-size: 15px; }
        .mac-badge { display: inline-block; background: #e3f2fd; color: #0d47a1; padding: 5px 12px; border-radius: 20px; font-size: 13px; font-weight: bold; margin-bottom: 10px; }
        .pw-box { display: flex; align-items: center; justify-content: space-between; background-color: #f4f6f8; padding: 10px 14px; border-radius: 8px; border: 1px dashed #2e7d32; margin: 12px 0; }
        .pw-text { font-family: monospace; font-weight: bold; font-size: 18px; color: #1b5e20; letter-spacing: 1px; user-select: all; }
        .copy-btn { background-color: #2e7d32; color: white; border: none; padding: 9px 14px; border-radius: 6px; font-size: 14px; font-weight: bold; cursor: pointer; }
        .copy-btn:active { transform: scale(0.96); }
        .toast { color: #2e7d32; font-size: 13px; font-weight: bold; height: 18px; margin-top: 4px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>✅ ĐỊNH DANH THÀNH CÔNG!</h2>
        <div class="mac-badge">Thiết bị VIP: {{ client_mac }}</div>
        <p>Hệ thống AI đang đánh thức mạng ưu tiên <b>{{ teacher_ssid }}</b> tại phòng học của Thầy/Cô.</p>
        <p style="margin-bottom: 4px;">Hãy bấm nút bên dưới để sao chép mật khẩu:</p>
        <div class="pw-box">
            <span id="wifiPw" class="pw-text">{{ teacher_pass }}</span>
            <button type="button" class="copy-btn" onclick="copyPassword()">📋 Sao chép</button>
        </div>
        <div id="copyStatus" class="toast"></div>
        <p style="font-size: 13px; color: #666; margin-top: 14px;">
            * Sau khi sao chép, hệ thống sẽ tự động ngắt mạng Onboarding để Thầy/Cô dán mật khẩu vào mạng <b>{{ teacher_ssid }}</b>.
        </p>
    </div>

    <script>
        function copyPassword() {
            var pwText = document.getElementById("wifiPw").innerText;
            var statusEl = document.getElementById("copyStatus");
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(pwText).then(function() {
                    statusEl.innerText = "✔️ Đã sao chép mật khẩu vào bộ nhớ tạm!";
                });
            } else {
                var tempInput = document.createElement("textarea");
                tempInput.value = pwText;
                document.body.appendChild(tempInput);
                tempInput.select();
                document.execCommand("copy");
                document.body.removeChild(tempInput);
                statusEl.innerText = "✔️ Đã sao chép mật khẩu vào bộ nhớ tạm!";
            }
        }
    </script>
</body>
</html>
"""


def get_client_mac(ip):
    """
    Tra cứu MAC từ IP của điện thoại Giáo viên:
    1. Kiểm tra bảng ARP nội bộ trên Server.
    2. Nếu chạy qua WSL2 (bị lệch lớp mạng), SSH vào Router Master (192.168.2.1)
       để tra trong /proc/net/arp và /tmp/dhcp.leases!
    """
    try:
        pid = os.popen(f"arp -n {ip} | awk '/^[0-9]/ {{print $3}}'")
        mac = pid.read().strip()
        if re.match(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", mac):
            return mac.upper()
    except Exception:
        pass

    # Fallback: Hỏi trực tiếp bảng ARP & DHCP Leases trên Router Master
    try:
        cmd = (
            f"awk '$1==\"{ip}\" && $4!=\"00:00:00:00:00:00\" {{print toupper($4)}}' /proc/net/arp; "
            f"awk '$3==\"{ip}\" {{print toupper($2)}}' /tmp/dhcp.leases"
        )
        out = actuators._exec_ssh_output(ROUTER_IP, ROUTER_USER, ROUTER_PASS, cmd)
        for line in out.splitlines():
            candidate = line.strip()
            if re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", candidate):
                return candidate
    except Exception:
        pass
    return None


def activate_teacher_ssid_and_kick(mac_address):
    """
    Chu trình khép kín sau khi Giáo viên nhận mật khẩu (sau 6 giây):
    1. Bật ngay mạng 'teacher_5g' (School_Teacher) trên các AP phòng học.
    2. Đá thiết bị khỏi mạng 'School_Onboarding' để điện thoại chuyển sang 'School_Teacher'.
    """
    try:
        node_ips = [ROUTER_IP]
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f)
                nodes = cfg.get("network_topology", {}).get("nodes", [])
                node_ips = [n["ip"] for n in nodes if n.get("zone_type", "classroom") == "classroom"] or [ROUTER_IP]

        for ip in node_ips:
            print(f"[*] Đang đánh thức mạng '{TEACHER_SSID}' trên AP {ip} cho GV ({mac_address})...")
            actuators.prune_unused_ssid(ip, ROUTER_USER, ROUTER_PASS, vap_section="teacher_5g", disable=False)

            # Tìm đúng interface đang phát School_Onboarding (VD: phy0-ap1) để kick client
            kick_cmd = (
                "for dev in $(iw dev | awk '$1==\"Interface\"{print $2}'); do "
                f"  ssid=$(iw dev $dev info | awk '$1==\"ssid\"{{print $2}}'); "
                f"  if [ \"$ssid\" = \"{ONBOARD_SSID}\" ]; then "
                f"    iw dev $dev station del {mac_address} 2>/dev/null; "
                "  fi; "
                "done"
            )
            actuators._exec_ssh_command(ip, ROUTER_USER, ROUTER_PASS, kick_cmd)
        print(f"[+] Hoàn tất mở mạng '{TEACHER_SSID}' và chuyển giao thiết bị {mac_address}!")
    except Exception as e:
        print(f"[-] Lỗi trong quá trình kích hoạt mạng Teacher: {e}")


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def captive_portal(path):
    if request.method == 'POST':
        gv_id = request.form.get('gv_id', '').strip()
        password = request.form.get('password', '').strip()

        with open(TEACHER_DB_FILE, 'r') as f:
            teacher_db = json.load(f)

        if gv_id in teacher_db and teacher_db[gv_id] == password:
            client_ip = request.remote_addr
            client_mac = get_client_mac(client_ip) or "D4:53:2A:VIP:01"

            with open(VIP_MAC_FILE, 'r') as f:
                vip_list = json.load(f)

            if client_mac not in vip_list:
                vip_list.append(client_mac)
                with open(VIP_MAC_FILE, 'w') as f:
                    json.dump(vip_list, f, indent=2)

            print(f"[+] Đã cấp quyền VIP cho: {client_mac} (GV: {gv_id}, IP: {client_ip})")

            # Cho Giáo viên 6 giây để bấm nút "Sao chép mật khẩu", sau đó mới bật mạng Teacher & ngắt Onboarding
            threading.Timer(6.0, activate_teacher_ssid_and_kick, args=[client_mac]).start()

            return render_template_string(
                SUCCESS_TEMPLATE,
                client_mac=client_mac,
                teacher_ssid=TEACHER_SSID,
                teacher_pass=TEACHER_PASS
            )
        else:
            return render_template_string(
                LOGIN_TEMPLATE,
                teacher_ssid=TEACHER_SSID,
                error="Sai Mã số Giáo viên hoặc Mật khẩu!"
            )

    return render_template_string(LOGIN_TEMPLATE, teacher_ssid=TEACHER_SSID)


if __name__ == '__main__':
    print("[*] Cổng Captive Portal Onboarding đang chạy ở cổng 80...")
    app.run(host='0.0.0.0', port=80)