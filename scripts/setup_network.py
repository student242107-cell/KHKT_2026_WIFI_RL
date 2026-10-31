import os
import yaml
import paramiko
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [PROVISIONING] - %(levelname)s - %(message)s")

load_dotenv()

ROUTER_USER = os.getenv("ROUTER_USER", "root")
ROUTER_PASS = os.getenv("ROUTER_PASS", "")
SSH_PORT = int(os.getenv("ROUTER_SSH_PORT", 22))

SSID_5G = os.getenv("WIFI_SSID_5G", "KHKT_Testbed 5GHz")
SSID_2G = os.getenv("WIFI_SSID_2G", "KHKT_Testbed 2.4 GHz")
WIFI_PASS = os.getenv("WIFI_PASSWORD", "0776642209")
WIFI_ENC = os.getenv("WIFI_ENCRYPTION", "psk2")

TEACHER_SSID = os.getenv("TEACHER_SSID", "School_Teacher")
TEACHER_PASS = os.getenv("TEACHER_PASSWORD", "TeacherPassword456")
ONBOARD_SSID = os.getenv("ONBOARD_SSID", "School_Onboarding")
MOB_DOMAIN = os.getenv("MOBILITY_DOMAIN", "abcd")
EDGE_SERVER_IP = os.getenv("EDGE_SERVER_IP", "192.168.2.229")

CONFIG_PATH = "configs/network_config.yaml"


def run_ssh_script(ip, commands, node_name):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        logging.info(f"Đang kết nối SSH tới {node_name} ({ip})...")
        ssh.connect(hostname=ip, port=SSH_PORT, username=ROUTER_USER, password=ROUTER_PASS, timeout=8)
        full_cmd = " && ".join(commands)
        _, stdout, stderr = ssh.exec_command(full_cmd)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code == 0:
            logging.info(f"✅ Đã cấu hình thành công {node_name} ({ip})!")
        else:
            err_msg = stderr.read().decode("utf-8", errors="ignore")
            logging.warning(f"⚠️ {node_name} ({ip}) trả về mã {exit_code}: {err_msg}")
        return True
    except Exception as e:
        logging.error(f"❌ Không thể kết nối tới {node_name} ({ip}): {e}")
        return False
    finally:
        ssh.close()


def build_wireless_commands(ssid_suffix="", zone_type="classroom"):
    """
    Quy hoạch Virtual BSSID/SSID thông minh theo Phân vùng (Zone Role):
    - classroom: Bật Student (5G+2.4G) + Bật Onboarding (2.4G) + Tắt tạm Teacher (5G) chờ kích hoạt động.
    - privileged_office (Phòng Giáo viên): Tắt Student, Chỉ bật Teacher (Always-On) + Hạ công suất 12dBm chống lọt sóng.
    """
    ssid_2g_full = f"{SSID_2G}{ssid_suffix}"
    ssid_5g_full = f"{SSID_5G}{ssid_suffix}"
    teacher_ssid_full = f"{TEACHER_SSID}{ssid_suffix}"

    is_office = (zone_type.lower() == "privileged_office")
    student_disabled = "1" if is_office else "0"
    onboard_disabled = "1" if is_office else "0"
    teacher_disabled = "0" if is_office else "1"  # Phòng học mặc định tỉa (ẩn) mạng Teacher khi chưa có GV
    txpower_5g = "12" if is_office else "20"      # Phòng GV giới hạn 12dBm để không lọt Beacon ra hành lang

    return [
        # 1. Cấu hình Phần cứng Radio0 (2.4GHz) & Mạng Học sinh 2.4GHz
        "uci set wireless.radio0.disabled='0'",
        "uci set wireless.radio0.band='2g'",
        "uci set wireless.radio0.channel='6'",
        "uci set wireless.radio0.htmode='HE20'",
        "uci set wireless.default_radio0.device='radio0'",
        "uci set wireless.default_radio0.mode='ap'",
        "uci set wireless.default_radio0.network='lan'",
        f"uci set wireless.default_radio0.ssid='{ssid_2g_full}'",
        f"uci set wireless.default_radio0.encryption='{WIFI_ENC}'",
        f"uci set wireless.default_radio0.key='{WIFI_PASS}'",
        f"uci set wireless.default_radio0.disabled='{student_disabled}'",

        # 2. Tạo Virtual AP 'onboarding_2g' trên Radio0 (Mạng Mở cho Giáo viên đăng ký MAC lần đầu)
        "uci set wireless.onboarding_2g=wifi-iface",
        "uci set wireless.onboarding_2g.device='radio0'",
        "uci set wireless.onboarding_2g.mode='ap'",
        "uci set wireless.onboarding_2g.network='onboard_net'",
        f"uci set wireless.onboarding_2g.ssid='{ONBOARD_SSID}'",
        "uci set wireless.onboarding_2g.encryption='none'",
        "uci set wireless.onboarding_2g.isolate='1'",
        f"uci set wireless.onboarding_2g.disabled='{onboard_disabled}'",

        # 3. Cấu hình Phần cứng Radio1 (5GHz) & Mạng Học sinh 5GHz
        "uci set wireless.radio1.disabled='0'",
        "uci set wireless.radio1.band='5g'",
        "uci set wireless.radio1.channel='36'",
        "uci set wireless.radio1.htmode='HE40'",
        f"uci set wireless.radio1.txpower='{txpower_5g}'",
        "uci set wireless.default_radio1.device='radio1'",
        "uci set wireless.default_radio1.mode='ap'",
        "uci set wireless.default_radio1.network='lan'",
        f"uci set wireless.default_radio1.ssid='{ssid_5g_full}'",
        f"uci set wireless.default_radio1.encryption='{WIFI_ENC}'",
        f"uci set wireless.default_radio1.key='{WIFI_PASS}'",
        "uci set wireless.default_radio1.ieee80211r='1'",
        f"uci set wireless.default_radio1.mobility_domain='{MOB_DOMAIN}'",
        "uci set wireless.default_radio1.ft_over_ds='0'",
        "uci set wireless.default_radio1.ft_psk_generate_local='1'",
        f"uci set wireless.default_radio1.disabled='{student_disabled}'",

        # 4. Tạo Virtual AP 'teacher_5g' trên Radio1 (Mạng Ưu tiên Giáo viên - Dynamic SSID Pruning)
        "uci set wireless.teacher_5g=wifi-iface",
        "uci set wireless.teacher_5g.device='radio1'",
        "uci set wireless.teacher_5g.mode='ap'",
        "uci set wireless.teacher_5g.network='lan'",
        f"uci set wireless.teacher_5g.ssid='{teacher_ssid_full}'",
        f"uci set wireless.teacher_5g.encryption='{WIFI_ENC}'",
        f"uci set wireless.teacher_5g.key='{TEACHER_PASS}'",
        "uci set wireless.teacher_5g.ieee80211r='1'",
        f"uci set wireless.teacher_5g.mobility_domain='{MOB_DOMAIN}'",
        "uci set wireless.teacher_5g.ft_over_ds='0'",
        "uci set wireless.teacher_5g.ft_psk_generate_local='1'",
        f"uci set wireless.teacher_5g.disabled='{teacher_disabled}'",

        "uci commit wireless"
    ]


def provision_master_node(node, gateway_ip):
    zone_type = node.get("zone_type", "classroom")

    cmds = [
        "uci set dhcp.lan.ignore='0'",
        "uci set dhcp.lan.start='100'",
        "uci set dhcp.lan.limit='150'",
        "uci set dhcp.lan.leasetime='12h'",
        
        # --- XÓA CẤU HÌNH DNS HIJACKING CŨ (NẾU CÒN SÓT) ---
        # DNS hijacking ảnh hưởng TẤT CẢ mạng → không dùng nữa
        "uci -q delete dhcp.@dnsmasq[0].address || true",
        "uci commit dhcp",
        "/etc/init.d/dnsmasq enable",
        "/etc/init.d/dnsmasq restart",
        
        # --- TẠO NETWORK INTERFACE RIÊNG CHO MẠNG ONBOARDING (ISOLATED) ---
        # Tạo bridge và interface riêng cho onboarding để cách ly khỏi LAN
        "uci set network.onboard_net=interface",
        "uci set network.onboard_net.proto='static'",
        f"uci set network.onboard_net.ipaddr='192.168.199.1'",
        "uci set network.onboard_net.netmask='255.255.255.0'",
        "uci set network.onboard_net.device='br-onboard'",
        "uci set network.onboard_br=device",
        "uci set network.onboard_br.type='bridge'",
        "uci set network.onboard_br.name='br-onboard'",
        "uci commit network",
        
        # Cấp DHCP cho client trên mạng Onboarding (dải 192.168.199.x)
        "uci set dhcp.onboard_net=dhcp",
        "uci set dhcp.onboard_net.interface='onboard_net'",
        "uci set dhcp.onboard_net.start='50'",
        "uci set dhcp.onboard_net.limit='50'",
        "uci set dhcp.onboard_net.leasetime='5m'",
        # DHCP Option 6: Trỏ DNS về chính router (để client query DNS qua router)
        f"uci add_list dhcp.onboard_net.dhcp_option='6,192.168.199.1'",
        "uci commit dhcp",
        
        # --- FIREWALL ZONE: CÁCH LY MẠNG ONBOARDING ---
        "uci set firewall.onboard_zone=zone",
        "uci set firewall.onboard_zone.name='onboard'",
        "uci set firewall.onboard_zone.input='ACCEPT'",
        "uci set firewall.onboard_zone.output='ACCEPT'",
        "uci set firewall.onboard_zone.forward='REJECT'",
        "uci set firewall.onboard_zone.network='onboard_net'",
        
        # Cho phép DHCP từ client Onboarding tới Router
        "uci set firewall.onboard_dhcp=rule",
        "uci set firewall.onboard_dhcp.name='Allow-Onboard-DHCP'",
        "uci set firewall.onboard_dhcp.src='onboard'",
        "uci set firewall.onboard_dhcp.proto='udp'",
        "uci set firewall.onboard_dhcp.dest_port='67-68'",
        "uci set firewall.onboard_dhcp.target='ACCEPT'",
        
        # Cho phép HTTP tới Edge Server (Captive Portal)
        "uci set firewall.onboard_http=rule",
        "uci set firewall.onboard_http.name='Allow-Onboard-HTTP-to-EdgeServer'",
        "uci set firewall.onboard_http.src='onboard'",
        "uci set firewall.onboard_http.dest='lan'",
        "uci set firewall.onboard_http.proto='tcp'",
        "uci set firewall.onboard_http.dest_port='80'",
        f"uci set firewall.onboard_http.dest_ip='{EDGE_SERVER_IP}'",
        "uci set firewall.onboard_http.target='ACCEPT'",
        
        # Forwarding: onboard -> lan (chỉ HTTP đã được rule ở trên cho phép)
        "uci set firewall.onboard_to_lan=forwarding",
        "uci set firewall.onboard_to_lan.src='onboard'",
        "uci set firewall.onboard_to_lan.dest='lan'",
        "uci set firewall.@zone[0].masq='1'",
        
        # --- DNS HIJACKING CHỈ DÀNH RIÊNG CHO MẠNG ONBOARD (Cổng 53 -> 5353) ---
        # Tuyệt đối không ảnh hưởng tới KHKT_Testbed 5GHz/2.4GHz
        "uci set firewall.onboard_dns_hijack=redirect",
        "uci set firewall.onboard_dns_hijack.name='Hijack-DNS-Onboard-Only'",
        "uci set firewall.onboard_dns_hijack.src='onboard'",
        "uci set firewall.onboard_dns_hijack.proto='udp tcp'",
        "uci set firewall.onboard_dns_hijack.src_dport='53'",
        "uci set firewall.onboard_dns_hijack.dest_port='5353'",
        "uci set firewall.onboard_dns_hijack.target='DNAT'",
        
        # --- CAPTIVE PORTAL REDIRECT (DNAT) - CHỈ ÁP DỤNG CHO MẠNG ONBOARDING ---
        "uci set firewall.captive_redirect=redirect",
        "uci set firewall.captive_redirect.name='Captive-Portal-Redirect'",
        "uci set firewall.captive_redirect.src='onboard'",
        "uci set firewall.captive_redirect.src_dport='80'",
        "uci set firewall.captive_redirect.proto='tcp'",
        f"uci set firewall.captive_redirect.dest_ip='{EDGE_SERVER_IP}'",
        "uci set firewall.captive_redirect.dest_port='80'",
        "uci set firewall.captive_redirect.dest='lan'",
        "uci set firewall.captive_redirect.target='DNAT'",
        
        "uci commit firewall",
        "/etc/init.d/firewall enable",
        "/etc/init.d/firewall restart",

        # --- KHỞI ĐỘNG BỘ PHÂN GIẢI DNS CÔ LẬP CHO ONBOARD (PORT 5353) & LƯU VÀO RC.LOCAL ---
        "sed -i '/5353/d' /etc/rc.local",
        "sed -i 's|^exit 0|killall -9 dnsmasq_onboard 2>/dev/null; /usr/sbin/dnsmasq --port=5353 --listen-address=192.168.199.1 --bind-interfaces --address=/#/192.168.199.1 --no-resolv --no-hosts\\nexit 0|' /etc/rc.local",
        "killall -9 dnsmasq_onboard 2>/dev/null || true",
        "pgrep -f 'dnsmasq.*5353' || /usr/sbin/dnsmasq --port=5353 --listen-address=192.168.199.1 --bind-interfaces --address=/#/192.168.199.1 --no-resolv --no-hosts",
    ] + build_wireless_commands(node.get("ssid_suffix", ""), zone_type) + ["wifi reload"]

    run_ssh_script(node["ip"], cmds, f"[MASTER - {zone_type.upper()}] {node['name']}")


def provision_dumb_ap_node(node, gateway_ip):
    zone_type = node.get("zone_type", "classroom")
    cmds = [
        f"uci set network.lan.gateway='{gateway_ip}'",
        f"uci set network.lan.dns='{gateway_ip}'",
        "uci -q del_list network.@device[0].ports='wan' || true",
        "uci add_list network.@device[0].ports='wan'",
        "uci -q delete network.wan || true",
        "uci -q delete network.wan6 || true",
        "uci commit network",
        "uci set dhcp.lan.ignore='1'",
        "uci -q delete dhcp.lan.ra || true",
        "uci -q delete dhcp.lan.dhcpv6 || true",
        "uci commit dhcp",
        "/etc/init.d/dnsmasq stop || true",
        "/etc/init.d/dnsmasq disable || true",
        "/etc/init.d/odhcpd stop || true",
        "/etc/init.d/odhcpd disable || true",
        "/etc/init.d/firewall stop || true",
        "/etc/init.d/firewall disable || true",
    ] + build_wireless_commands(node.get("ssid_suffix", ""), zone_type) + [
        "/etc/init.d/network reload",
        "wifi reload"
    ]

    run_ssh_script(node["ip"], cmds, f"[DUMB-AP - {zone_type.upper()}] {node['name']}")


def main():
    if not os.path.exists(CONFIG_PATH):
        logging.critical(f"Không tìm thấy file cấu hình {CONFIG_PATH}!")
        return

    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)

    topo = config.get("network_topology", {})
    gateway_ip = topo.get("gateway_ip", "192.168.2.1")
    nodes = topo.get("nodes", [])

    print("==================================================================")
    print(f" 🚀 AUTO-PROVISIONING: QUY HOẠCH {len(nodes)} THIẾT BỊ & VIRTUAL SSIDs")
    print("==================================================================")

    for node in nodes:
        role = node.get("role", "ap").lower()
        if role == "master":
            provision_master_node(node, gateway_ip)
        else:
            provision_dumb_ap_node(node, gateway_ip)


if __name__ == "__main__":
    main()