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


def build_wireless_commands(ssid_suffix=""):
    """Tạo bộ lệnh chuẩn hóa Wi-Fi 6 (802.11ax) + Fast Roaming (802.11r) cho Xiaomi AX3000T."""
    ssid_2g_full = f"{SSID_2G}{ssid_suffix}"
    ssid_5g_full = f"{SSID_5G}{ssid_suffix}"

    return [
        # Cấu hình Radio0 (2.4GHz)
        "uci set wireless.radio0.disabled='0'",
        "uci set wireless.radio0.band='2g'",
        "uci set wireless.radio0.htmode='HE20'",
        "uci set wireless.default_radio0.mode='ap'",
        "uci set wireless.default_radio0.network='lan'",
        f"uci set wireless.default_radio0.ssid='{ssid_2g_full}'",
        f"uci set wireless.default_radio0.encryption='{WIFI_ENC}'",
        f"uci set wireless.default_radio0.key='{WIFI_PASS}'",
        "uci set wireless.default_radio0.ieee80211r='1'",
        f"uci set wireless.default_radio0.mobility_domain='{MOB_DOMAIN}'",
        "uci set wireless.default_radio0.ft_over_ds='0'",
        "uci set wireless.default_radio0.ft_psk_generate_local='1'",

        # Cấu hình Radio1 (5GHz)
        "uci set wireless.radio1.disabled='0'",
        "uci set wireless.radio1.band='5g'",
        "uci set wireless.radio1.htmode='HE40'",
        "uci set wireless.default_radio1.mode='ap'",
        "uci set wireless.default_radio1.network='lan'",
        f"uci set wireless.default_radio1.ssid='{ssid_5g_full}'",
        f"uci set wireless.default_radio1.encryption='{WIFI_ENC}'",
        f"uci set wireless.default_radio1.key='{WIFI_PASS}'",
        "uci set wireless.default_radio1.ieee80211r='1'",
        f"uci set wireless.default_radio1.mobility_domain='{MOB_DOMAIN}'",
        "uci set wireless.default_radio1.ft_over_ds='0'",
        "uci set wireless.default_radio1.ft_psk_generate_local='1'",
        "uci commit wireless"
    ]


def provision_master_node(node, gateway_ip):
    """Cấu hình cho Router Master: Bật DHCP Server và giữ vai trò Gateway chính."""
    cmds = [
        "uci set dhcp.lan.ignore='0'",
        "uci set dhcp.lan.start='100'",
        "uci set dhcp.lan.limit='150'",
        "uci set dhcp.lan.leasetime='12h'",
        "uci commit dhcp",
        "/etc/init.d/dnsmasq enable",
        "/etc/init.d/dnsmasq restart"
    ] + build_wireless_commands(node.get("ssid_suffix", "")) + ["wifi reload"]

    run_ssh_script(node["ip"], cmds, f"[MASTER] {node['name']}")


def provision_dumb_ap_node(node, gateway_ip):
    """
    Cấu hình cho các Router phụ (AP1, AP2, ... AP_N) thành Dumb AP thuần túy:
    1. Tắt hoàn toàn DHCPv4 và DHCPv6 trên AP để không xung đột với Master.
    2. Trỏ Default Gateway và DNS về Master (192.168.2.1).
    3. Gộp cổng WAN vào br-lan (DSA Bridge) và xóa giao diện wan/wan6 để cắm cổng nào cũng là LAN.
    4. Tắt Firewall trên AP để Laptop Edge Server SSH/Telemetry xuyên suốt.
    """
    cmds = [
        # 1. Trỏ Gateway & DNS về Router Master
        f"uci set network.lan.gateway='{gateway_ip}'",
        f"uci set network.lan.dns='{gateway_ip}'",

        # 2. Gộp cổng vật lý 'wan' vào chung cầu nối 'br-lan' (Biến cổng WAN thành cổng LAN thứ 4)
        "uci -q del_list network.@device[0].ports='wan' || true",
        "uci add_list network.@device[0].ports='wan'",
        "uci -q delete network.wan || true",
        "uci -q delete network.wan6 || true",
        "uci commit network",

        # 3. Tắt cấp phát IP (DHCPv4 & IPv6 RA/DHCPv6) trên AP
        "uci set dhcp.lan.ignore='1'",
        "uci -q delete dhcp.lan.ra || true",
        "uci -q delete dhcp.lan.dhcpv6 || true",
        "uci commit dhcp",

        # 4. Vô hiệu hóa dnsmasq, odhcpd và firewall để hoạt động như AP Layer-2 thuần túy
        "/etc/init.d/dnsmasq stop || true",
        "/etc/init.d/dnsmasq disable || true",
        "/etc/init.d/odhcpd stop || true",
        "/etc/init.d/odhcpd disable || true",
        "/etc/init.d/firewall stop || true",
        "/etc/init.d/firewall disable || true",
    ] + build_wireless_commands(node.get("ssid_suffix", "")) + [
        "/etc/init.d/network reload",
        "wifi reload"
    ]

    run_ssh_script(node["ip"], cmds, f"[DUMB-AP] {node['name']}")


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
    print(f" 🚀 AUTO-PROVISIONING: QUY HOẠCH {len(nodes)} THIẾT BỊ TRONG TOPOLOGY")
    print("==================================================================")

    for node in nodes:
        role = node.get("role", "ap").lower()
        if role == "master":
            provision_master_node(node, gateway_ip)
        else:
            provision_dumb_ap_node(node, gateway_ip)


if __name__ == "__main__":
    main()