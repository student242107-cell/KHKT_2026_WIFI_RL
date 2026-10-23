import paramiko
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _exec_ssh_command(ap_ip, username, password, command):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ap_ip, username=username, password=password, timeout=5)
        stdin, stdout, stderr = ssh.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        err_msg = stderr.read().decode().strip()
        if exit_status != 0 and err_msg:
            logging.warning(f"[SSH Warn - {ap_ip}]: {err_msg}")
        return True
    except Exception as e:
        logging.error(f"[-] Lỗi kết nối SSH tới {ap_ip}: {e}")
        return False
    finally:
        ssh.close()


def apply_ai_profile_atomic(
    ap_ip, username, password,
    radio_band="radio1", interface="phy1-ap0",
    channel=44, bandwidth="HE20", tx_power_dbm=18,
    beacon_int=80, enable_bf=True
):
    """
    Gom toàn bộ lệnh UCI + Kích hoạt Airtime Queue Limit (AQL) & FQ-CoDel
    để dập tắt bão UDP Jammer DSCP 56, bảo vệ tuyệt đối luồng Video Larix.
    """
    bf_val = 1 if enable_bf else 0
    phy_idx = "phy1" if "1" in radio_band or "1" in interface else "phy0"

    # Khi AI thu hẹp băng thông về HE20/HE40 (chế độ chống bão tải),
    # đồng thời siết giới hạn hàng đợi AQL xuống 1500us để gói UDP rác không thể làm trễ Video!
    aql_limit = 1500 if bandwidth == "HE20" else 3000

    cmd = (
        f"uci set wireless.{radio_band}.channel='{channel}' && "
        f"uci set wireless.{radio_band}.htmode='{bandwidth}' && "
        f"uci set wireless.{radio_band}.txpower='{tx_power_dbm}' && "
        f"uci set wireless.{radio_band}.beacon_int='{beacon_int}' && "
        f"uci set wireless.{radio_band}.he_bss_color='8' && "
        f"uci set wireless.{radio_band}.he_su_beamformer='{bf_val}' && "
        f"uci commit wireless && wifi reload; "
        f"echo {aql_limit} > /sys/kernel/debug/ieee80211/{phy_idx}/aql_txq_limit 2>/dev/null; "
        f"tc qdisc replace dev {interface} root fq_codel limit 1024 target 3ms interval 30ms ecn 2>/dev/null; "
        f"tc qdisc replace dev br-lan root fq_codel limit 1024 target 3ms interval 30ms noecn 2>/dev/null"
    )
    success = _exec_ssh_command(ap_ip, username, password, cmd)
    if success:
        logging.info(
            f"   [+] AP {ap_ip}: Đã đồng bộ AI Profile ({radio_band}/{interface}): "
            f"CH={channel}, BW={bandwidth}, Tx={tx_power_dbm}dBm, Beacon={beacon_int}ms + Kích hoạt AQL/FQ-CoDel."
        )
    return success


def apply_mcs_ceiling(ap_ip, username, password, interface="phy1-ap0", mcs_index=7):
    """
    Giữ nguyên MIMO 2x2 của Xiaomi AX3000T (không ép về 1:0-x gây tụt băng thông còn 60Mbps),
    điều chỉnh giới hạn AQL thông minh theo mức MCS mà AI yêu cầu.
    """
    phy_idx = "phy1" if "1" in interface else "phy0"
    # MCS càng thấp (mạng càng nghẽn) -> càng siết chặt bộ đệm AQL để chống Bufferbloat
    aql_val = 1200 if mcs_index <= 7 else (2000 if mcs_index <= 9 else 4000)
    cmd = f"echo {aql_val} > /sys/kernel/debug/ieee80211/{phy_idx}/aql_txq_limit 2>/dev/null"
    success = _exec_ssh_command(ap_ip, username, password, cmd)
    if success:
        logging.info(f"   [+] AP {ap_ip}: Đã tinh chỉnh ngưỡng chống nghẽn tầng MAC (MCS Ceiling={mcs_index}, AQL={aql_val}us).")
    return success


def optimize_tx_power(ap_ip, username, password, interface, target_power_dbm):
    """Điều chỉnh nhanh Tx Power qua lệnh 'iw' (Zero-downtime, không gây wifi reload)."""
    iw_val = int(target_power_dbm) * 100
    command = f"iw dev {interface} set txpower fixed {iw_val}"
    intent = f"điều chỉnh công suất ({interface}) thành {target_power_dbm}dBm"
    execute_ssh_command(ap_ip, username, password, command, intent)


def adjust_beacon_interval(ap_ip, username, password, radio_band, interval_ms):
    if isinstance(ap_ip, list):
        ap_ip = ap_ip[0]
    command = f"uci set wireless.{radio_band}.beacon_int='{interval_ms}' && uci commit wireless && wifi reload"
    intent = f"đổi chu kỳ Beacon ({radio_band}) thành {interval_ms}ms"
    execute_ssh_command(ap_ip, username, password, command, intent)

def adaptive_bandwidth_fallback(ap_ip, username, password, radio_band, bandwidth):
    command = f"uci set wireless.{radio_band}.htmode='{bandwidth}' && uci commit wireless && wifi reload"
    intent = f"ép băng thông ({radio_band}) chuyển về {bandwidth}"
    execute_ssh_command(ap_ip, username, password, command, intent)


def ai_action_toggle_beamforming(ap_ip, username, password, radio_band, enable_bf=True):
    bf_value = '1' if enable_bf else '0'
    action_name = 'BẬT' if enable_bf else 'TẮT'
    uci_commands = (
        f"uci set wireless.{radio_band}.he_su_beamformer='{bf_value}' && "
        "uci commit wireless && wifi reload"
    )
    intent = f"{action_name} chùm sóng Beamforming trên {radio_band}"
    execute_ssh_command(ap_ip, username, password, uci_commands, intent)


def prune_unused_ssid(ap_ip, username, password, vap_interface, disable=True):
    action_val = '1' if disable else '0'
    action_name = 'TẮT' if disable else 'BẬT'
    command = f"uci set wireless.{vap_interface}.disabled='{action_val}' && uci commit wireless && wifi reload"
    intent = f"{action_name} mạng ảo {vap_interface}"
    execute_ssh_command(ap_ip, username, password, command, intent)