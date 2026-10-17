import os
import re
import socket
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml
import paramiko
from dotenv import load_dotenv

load_dotenv()

CONFIG_PATH = "configs/network_config.yaml"
ROUTER_USER = os.getenv("ROUTER_USER", "root")
ROUTER_PASS = os.getenv("ROUTER_PASS", "")
SSH_PORT = int(os.getenv("ROUTER_SSH_PORT", 22))
DEFAULT_MASTER_IP = os.getenv("MASTER_ROUTER_IP", "192.168.2.1")


def check_ssh_port_open(ip, port=22, timeout=0.8):
    """Kiểm tra nhanh xem IP có mở cổng SSH (22) hay không (Chạy mượt trên cả WSL2 & Linux)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def inspect_openwrt_node(ip):
    """
    Đăng nhập SSH vào thiết bị OpenWrt để trích xuất chính xác:
    - Hostname của thiết bị
    - Địa chỉ MAC (BSSID) của card 5GHz (phy1-ap0)
    - Địa chỉ MAC (BSSID) của card 2.4GHz (phy0-ap0)
    """
    if not check_ssh_port_open(ip, SSH_PORT):
        return None

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(hostname=ip, port=SSH_PORT, username=ROUTER_USER, password=ROUTER_PASS, timeout=3)
        cmd = "uci -q get system.@system[0].hostname; echo '===IW==='; iw dev"
        _, stdout, _ = ssh.exec_command(cmd)
        output = stdout.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    finally:
        ssh.close()

    if "===IW===" not in output:
        return None

    parts = output.split("===IW===")
    hostname = parts[0].strip() or f"OpenWrt_{ip.split('.')[-1]}"
    iw_out = parts[1]

    # Trích xuất cặp Interface và địa chỉ MAC (BSSID) thực tế từ 'iw dev'
    # Bắt chính xác cấu trúc: Interface <name> ... addr <mac>
    iface_blocks = re.findall(
        r"Interface\s+([^\s]+)\s+.*?addr\s+([0-9a-fA-F:]{17})",
        iw_out,
         flags=re.DOTALL
    )

    bssid_5g = None
    bssid_2g = None

    for iface_name, mac_addr in iface_blocks:
        mac_upper = mac_addr.upper()
        if "phy1" in iface_name or "wlan1" in iface_name:
            bssid_5g = mac_upper
        elif "phy0" in iface_name or "wlan0" in iface_name:
            bssid_2g = mac_upper

    if not bssid_5g and not bssid_2g:
        return None

    return {
        "ip": ip,
        "hostname": hostname,
        "bssid_5g": bssid_5g,
        "bssid_2g": bssid_2g,
    }


def discover_openwrt_topology(subnet_prefix="192.168.2", start_ip=1, end_ip=254, master_ip="192.168.2.1"):
    """Quét đa luồng toàn bộ dải mạng LAN để tìm tất cả các Router OpenWrt."""
    target_ips = [f"{subnet_prefix}.{i}" for i in range(start_ip, end_ip + 1)]
    print(f"[*] Đang phóng 50 luồng quét dải mạng {subnet_prefix}.{start_ip}-{end_ip} (Master chỉ định: {master_ip})...")

    discovered_nodes = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        future_to_ip = {executor.submit(inspect_openwrt_node, ip): ip for ip in target_ips}
        for future in as_completed(future_to_ip):
            info = future.result()
            if info:
                discovered_nodes.append(info)

    # Sắp xếp các thiết bị tìm được theo thứ tự IP tăng dần
    discovered_nodes.sort(key=lambda x: int(x["ip"].split(".")[-1]))
    return discovered_nodes


def build_and_save_yaml(discovered_nodes, master_ip, subnet_mask="255.255.255.0"):
    """Tự động phân vai Master vs. Dumb AP và sinh file configs/network_config.yaml."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)

    nodes_list = []
    ap_map_5g = {}
    ap_map_2g = {}
    ap_counter = 1

    print(f"\n[+] Đã phát hiện {len(discovered_nodes)} trạm phát OpenWrt trong mạng:")

    for item in discovered_nodes:
        ip = item["ip"]
        bssid_5g = item["bssid_5g"]
        bssid_2g = item["bssid_2g"]

        if ip == master_ip:
            role = "master"
            name = "Router_Master"
            ssid_suffix = ""
        else:
            role = "ap"
            name = f"Router_AP{ap_counter}"
            ssid_suffix = f" AP{ap_counter}"
            ap_counter += 1

        nodes_list.append({
            "name": name,
            "ip": ip,
            "role": role,
            "ssid_suffix": ssid_suffix,
            "bssid_5g": bssid_5g,
            "bssid_2g": bssid_2g,
        })

        if bssid_5g:
            ap_map_5g[bssid_5g] = ip
        if bssid_2g:
            ap_map_2g[bssid_2g] = ip

        print(
            f"   -> [{role.upper():6}] {name:13} | IP: {ip:13} | "
            f"5GHz BSSID: {bssid_5g or 'N/A':17} | 2.4GHz BSSID: {bssid_2g or 'N/A'}"
        )

    yaml_data = {
        "network_topology": {
            "gateway_ip": master_ip,
            "subnet_mask": subnet_mask,
            "nodes": nodes_list,
            "ap_map": ap_map_5g,
            "ap_map_2g": ap_map_2g,
        }
    }

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"\n✅ Đã tự động khởi tạo file cấu hình mạng chuẩn xác tại: {CONFIG_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="🔍 Zero-Touch OpenWrt Topology Auto-Discovery")
    parser.add_argument("--subnet", type=str, default="192.168.2", help="3 octet đầu của dải mạng (VD: 192.168.2)")
    parser.add_argument("--master-ip", type=str, default=DEFAULT_MASTER_IP, help="IP của Router Master (Mặc định: 192.168.2.1)")
    parser.add_argument("--start", type=int, default=1, help="IP bắt đầu quét (Mặc định: 1)")
    parser.add_argument("--end", type=int, default=20, help="IP kết thúc quét (Mặc định: 20 cho nhanh, hoặc 254 cho toàn mạng)")
    args = parser.parse_args()

    nodes = discover_openwrt_topology(
        subnet_prefix=args.subnet,
        start_ip=args.start,
        end_ip=args.end,
        master_ip=args.master_ip,
    )

    if nodes:
        build_and_save_yaml(nodes, master_ip=args.master_ip)
    else:
        print("[-] Không tìm thấy Router OpenWrt nào! Hãy kiểm tra lại dây LAN và mật khẩu ROUTER_PASS trong file .env.")