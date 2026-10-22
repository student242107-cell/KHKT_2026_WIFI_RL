import time
import json
import requests
import paramiko
import subprocess
import os
import logging
import yaml
from dotenv import load_dotenv

try:
    from netmiko import ConnectHandler
except ImportError:
    ConnectHandler = None
    logging.warning("Chưa cài netmiko. Chạy 'pip install netmiko' nếu dùng tính năng PoE Switch Hard-Reboot.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [WATCHDOG] - %(levelname)s - %(message)s')

# =====================================================================
# 1. NẠP CẤU HÌNH HỆ THỐNG & TÔPÔ MẠNG
# =====================================================================
load_dotenv()
ROUTER_USER = os.getenv("ROUTER_USER", "root")
ROUTER_PASS = os.getenv("ROUTER_PASS", "")
SSH_PORT = int(os.getenv("ROUTER_SSH_PORT", 22))

HARD_FAILSAFE_MODE = os.getenv("HARD_FAILSAFE_MODE", "SONOFF")

SONOFF_IP = os.getenv("SONOFF_IP", "192.168.2.250")
SWITCH_IP = os.getenv("SWITCH_IP", "192.168.2.254")
SWITCH_USER = os.getenv("SWITCH_USER", "admin")
SWITCH_PASS = os.getenv("SWITCH_PASS", "admin123")
SWITCH_TYPE = os.getenv("SWITCH_TYPE", "cisco_ios")

CONFIG_PATH = "configs/network_config.yaml"
PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"
FAILSAFE_LOG_PATH = "data/processed/failsafe_events.log"


def load_unique_aps():
    """
    Tải danh sách các AP vật lý (IP duy nhất) từ configs/network_config.yaml,
    kèm theo BSSID của cả 2 băng tần 2.4GHz (radio0) và 5GHz (radio1).
    """
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
            topo = config.get('network_topology', {})
            nodes = topo.get('nodes', [])

            unique_ips = {}
            if nodes:
                for idx, node in enumerate(nodes, start=1):
                    ip = node.get("ip")
                    if ip:
                        unique_ips[ip] = {
                            "name": node.get("name", f"AP_{ip}"),
                            "bssid_2g": node.get("bssid_2g"),
                            "bssid_5g": node.get("bssid_5g"),
                            "consecutive_fails": 0,
                            "poe_port": f"Gi1/0/{idx}"
                        }
                return unique_ips

            # Fallback nếu file YAML chỉ có ap_map
            ap_map_5g = topo.get('ap_map', {})
            ap_map_2g = topo.get('ap_map_2g', {})
            for idx, (mac, ip) in enumerate(ap_map_5g.items(), start=1):
                unique_ips.setdefault(ip, {
                    "name": f"AP_{ip}",
                    "bssid_2g": None,
                    "bssid_5g": mac.upper(),
                    "consecutive_fails": 0,
                    "poe_port": f"Gi1/0/{idx}"
                })
            for mac, ip in ap_map_2g.items():
                if ip in unique_ips:
                    unique_ips[ip]["bssid_2g"] = mac.upper()
            return unique_ips
    except Exception as e:
        logging.error(f"Lỗi đọc file YAML: {e}")
        return {}


ap_health_monitor = load_unique_aps()


# =====================================================================
# 2. CÁC HÀM TIỆN ÍCH GIÁM SÁT (SENSING) & GHI NHẬN HÌNH PHẠT
# =====================================================================

def log_failsafe_event(ap_ip, level, reason=""):
    """Ghi nhận sự kiện sập mạng để AI PPO đọc và áp dụng Penalty M>>N."""
    try:
        os.makedirs(os.path.dirname(FAILSAFE_LOG_PATH), exist_ok=True)
        with open(FAILSAFE_LOG_PATH, 'a') as f:
            f.write(f"{time.time()},{ap_ip},{level},{reason}\n")
    except Exception:
        pass


def check_ap_latency(ap_ip):
    """Đo độ trễ mạng (Latency/RTT) bằng kỹ thuật Multi-packet Burst (3 gói ICMP)."""
    try:
        cmd = f"ping -c 3 -W 2 -q {ap_ip}"
        output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8')

        if "packet loss" in output:
            loss_str = output.split("packet loss")[0].split(",")[-1].strip().replace("%", "")
            packet_loss = float(loss_str)
            if packet_loss == 100.0:
                return 9999.0

        if "min/avg/max" in output:
            rtt_stats = output.split("=")[1].split("ms")[0].strip()
            avg_latency = float(rtt_stats.split("/")[1])
            return avg_latency

    except subprocess.CalledProcessError:
        return 9999.0
    except Exception as e:
        logging.error(f"[!] Lỗi phân tích Latency trên AP {ap_ip}: {e}")

    return 9999.0


def check_mac_layer_anomaly(bssid_2g, bssid_5g):
    """
    Kiểm tra xem p_c (xác suất va chạm tầng MAC) trên radio0 (2.4G) hoặc radio1 (5G)
    có vượt ngưỡng nguy kịch (> 0.85) do cấu hình sai của AI hay không.
    """
    if not os.path.exists(PROCESSED_DATA_PATH):
        return False, 0.0
    try:
        with open(PROCESSED_DATA_PATH, 'r') as f:
            data = json.load(f)
        pc_2g = data.get(bssid_2g, {}).get("p_c", 0.0) if bssid_2g else 0.0
        pc_5g = data.get(bssid_5g, {}).get("p_c", 0.0) if bssid_5g else 0.0
        max_pc = max(pc_2g, pc_5g)
        return (max_pc > 0.85), max_pc
    except Exception:
        return False, 0.0


# =====================================================================
# 3. CƠ CHẾ DUAL FAIL-SAFE (SOFT & HARD RESCUE CHO CẢ RADIO0 VÀ RADIO1)
# =====================================================================

def soft_reset_ap(ap_ip):
    """
    CỨU HỘ MỀM (SOFT RESCUE):
    Khôi phục Safe-fallback Profile cho CẢ 2 BĂNG TẦN trên Xiaomi AX3000T:
    - radio0 (2.4GHz / phy0-ap0): Kênh 1, HE20, TxPower 14dBm, Beacon 100ms
    - radio1 (5GHz   / phy1-ap0): Kênh 36, HE20 (chống nghẽn), TxPower 16dBm, Beacon 100ms
    - Mở khóa trần tốc độ MCS (Auto Rate Adaptation) trên cả phy0-ap0 và phy1-ap0.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        logging.info(f"[SOFT-RESCUE] Đang SSH vào AP {ap_ip} để khôi phục Safe-Fallback cho cả radio0 (2.4G) & radio1 (5G)...")
        ssh.connect(ap_ip, port=SSH_PORT, username=ROUTER_USER, password=ROUTER_PASS, timeout=5)

        rescue_cmd = (
            # 1. Khôi phục cấu hình an toàn cho radio0 (2.4 GHz)
            "uci set wireless.radio0.disabled='0' && "
            "uci set wireless.radio0.channel='1' && "
            "uci set wireless.radio0.htmode='HE20' && "
            "uci set wireless.radio0.txpower='14' && "
            "uci set wireless.radio0.beacon_int='100' && "

            # 2. Khôi phục cấu hình an toàn cho radio1 (5 GHz)
            "uci set wireless.radio1.disabled='0' && "
            "uci set wireless.radio1.channel='36' && "
            "uci set wireless.radio1.htmode='HE20' && "
            "uci set wireless.radio1.txpower='16' && "
            "uci set wireless.radio1.beacon_int='100' && "

            # 3. Áp dụng cấu hình UCI và mở khóa bộ giới hạn MCS trên cả 2 card mạng
            "uci commit wireless && wifi reload && sleep 2 && "
            "iw dev phy0-ap0 set bitrates 2>/dev/null || true && "
            "iw dev phy1-ap0 set bitrates 2>/dev/null || true"
        )

        _, stdout, _ = ssh.exec_command(rescue_cmd)
        stdout.channel.recv_exit_status()  # Đợi lệnh chạy xong hoàn toàn trên Router
        log_failsafe_event(ap_ip, level="SOFT", reason="DualBand_SafeFallback_Restored")
        logging.info(f"✅ [SOFT-RESCUE SUCCESS] Đã khôi phục radio0 & radio1 trên AP {ap_ip} về trạng thái an toàn!")
        return True
    except Exception as e:
        logging.error(f"[SOFT-RESCUE FAILED] AP {ap_ip} đã treo cứng (Kernel Panic / Mất SSH): {e}")
        return False
    finally:
        ssh.close()


def hard_reboot_ap(ap_ip, poe_port):
    """
    CỨU HỘ CỨNG: Ngắt điện vật lý 220V (Sonoff Smart Plug S26AI)
    hoặc PoE Switch khi Soft-Rescue bất lực.

    Sonoff S26AI kết nối qua Mobile Hotspot (out-of-band channel),
    đảm bảo vẫn nhận lệnh ngay cả khi router chính bị treo.

    Flow: TẮT nguồn → Chờ 5 giây → BẬT lại → Router power cycle.
    """
    log_failsafe_event(ap_ip, level="HARD", reason="Hardware_Power_Cycle")

    if HARD_FAILSAFE_MODE == "SONOFF":
        logging.info(f"[HARD-RESCUE] Kích hoạt Sonoff Smart Plug S26AI cắt điện 220V cho AP {ap_ip}...")
        try:
            from src.modules.sonoff_controller import hard_reboot_router_via_sonoff
            success = hard_reboot_router_via_sonoff(delay=5, use_pulse=False)
            if success:
                logging.info(f"✅ [HARD-RESCUE] Power Cycle qua Sonoff thành công cho AP {ap_ip}!")
            else:
                logging.critical(
                    f"❌ [HARD-RESCUE] Power Cycle qua Sonoff THẤT BẠI cho AP {ap_ip}! "
                    f"Kiểm tra: Mobile Hotspot, Sonoff IP, DIY Mode."
                )
            return success
        except ImportError:
            # Fallback: gọi API trực tiếp nếu module chưa có
            logging.warning("[HARD-RESCUE] Fallback: Gọi Sonoff API trực tiếp (không qua controller)...")
            try:
                payload = {
                    "deviceid": "",
                    "data": {"switch": "off"}
                }
                requests.post(f"http://{SONOFF_IP}:8081/zeroconf/switch", json=payload, timeout=5)
                time.sleep(5)
                payload["data"]["switch"] = "on"
                requests.post(f"http://{SONOFF_IP}:8081/zeroconf/switch", json=payload, timeout=5)
                return True
            except Exception as e:
                logging.critical(f"[-] Lỗi Sonoff (fallback): {e}")
                return False
        except Exception as e:
            logging.critical(f"[-] Lỗi Sonoff Controller: {e}")
            return False

    elif HARD_FAILSAFE_MODE == "POE_SWITCH" and ConnectHandler is not None:
        logging.info(f"[HARD-RESCUE] Đăng nhập PoE Switch {SWITCH_IP}, cắt điện cổng {poe_port}...")
        try:
            net_connect = ConnectHandler(
                device_type=SWITCH_TYPE, ip=SWITCH_IP,
                username=SWITCH_USER, password=SWITCH_PASS
            )
            net_connect.enable()
            net_connect.send_config_set([f"interface {poe_port}", "power inline never"])
            time.sleep(5)
            net_connect.send_config_set([f"interface {poe_port}", "power inline auto"])
            net_connect.disconnect()
            return True
        except Exception as e:
            logging.critical(f"[-] Lỗi PoE Switch: {e}")
            return False
    return False


# =====================================================================
# 4. VÒNG LẶP ĐIỀU PHỐI (ORCHESTRATOR LOOP)
# =====================================================================

def main():
    print("==================================================================")
    print(" 🛡️ COGNITIVE WI-FI: DUAL-BAND FAIL-SAFE WATCHDOG DAEMON 🛡️")
    print(f" [*] Đang giám sát {len(ap_health_monitor)} Access Points (radio0 2.4G & radio1 5G).")
    print(f" [*] Chế độ Cứu hộ Cứng (Hard-Mode): {HARD_FAILSAFE_MODE}")
    print("==================================================================")

    if not ap_health_monitor:
        logging.critical("Chưa có cấu trúc mạng trong YAML. Hãy chạy auto_discover_aps.py trước!")
        return

    while True:
        try:
            for ap_ip, info in ap_health_monitor.items():
                latency = check_ap_latency(ap_ip)
                mac_overloaded, max_pc = check_mac_layer_anomaly(info["bssid_2g"], info["bssid_5g"])

                if latency > 500.0 or mac_overloaded:
                    info["consecutive_fails"] += 1
                    fails = info["consecutive_fails"]
                    logging.warning(
                        f"⚠️ {info['name']} ({ap_ip}) bất thường! "
                        f"Latency: {latency:.1f}ms | Max p_c: {max_pc:.2f} ({fails}/6)"
                    )

                    if fails >= 6:
                        logging.critical(f"🚨 {info['name']} ({ap_ip}) VƯỢT NGƯỠNG AN TOÀN! Kích hoạt giải cứu kép...")

                        if not soft_reset_ap(ap_ip):
                            hard_reboot_ap(ap_ip, info["poe_port"])

                        info["consecutive_fails"] = 0
                        logging.info(f"⏳ Đang đợi {info['name']} ({ap_ip}) ổn định lại sóng vô tuyến (30s)...")
                        time.sleep(30)
                else:
                    if info["consecutive_fails"] > 0:
                        logging.info(f"✅ {info['name']} ({ap_ip}) đã ổn định trở lại (Latency: {latency:.1f}ms).")
                    info["consecutive_fails"] = 0

            time.sleep(5)

        except KeyboardInterrupt:
            print("\n[+] Đã tắt hệ thống Watchdog.")
            break
        except Exception as e:
            logging.error(f"Lỗi Daemon: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()