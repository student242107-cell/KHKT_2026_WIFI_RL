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
        "uci set wireless.onboarding_2g.network='lan'",
        f"uci set wireless.onboarding_2g.ssid='{ONBOARD_SSID}'",
        "uci set wireless.onboarding_2g.encryption='none'",
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
    
    # IP của Laptop Edge Server đang chạy cổng Captive Portal (app.py)
    EDGE_SERVER_IP = "192.168.2.158"

    cmds = [
        "uci set dhcp.lan.ignore='0'",
        "uci set dhcp.lan.start='100'",
        "uci set dhcp.lan.limit='150'",
        "uci set dhcp.lan.leasetime='12h'",
        
        # --- CƠ CHẾ TỰ ĐỘNG BẬT POP-UP CAPTIVE PORTAL (DNS HIJACKING) ---
        "uci -q delete dhcp.@dnsmasq[0].address || true",
        f"uci add_list dhcp.@dnsmasq[0].address='/captive.apple.com/{EDGE_SERVER_IP}'",
        f"uci add_list dhcp.@dnsmasq[0].address='/connectivitycheck.gstatic.com/{EDGE_SERVER_IP}'",
        f"uci add_list dhcp.@dnsmasq[0].address='/clients3.google.com/{EDGE_SERVER_IP}'",
        f"uci add_list dhcp.@dnsmasq[0].address='/www.msftconnecttest.com/{EDGE_SERVER_IP}'",
        f"uci add_list dhcp.@dnsmasq[0].address='/ipv6.msftconnecttest.com/{EDGE_SERVER_IP}'",
        
        "uci commit dhcp",
        "/etc/init.d/dnsmasq enable",
        "/etc/init.d/dnsmasq restart"
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