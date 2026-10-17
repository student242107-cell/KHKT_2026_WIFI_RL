import paramiko
import time
import logging

def execute_ssh_command(ip, username, password, command, intent=""):
    """
    Hàm lõi để thực thi SSH an toàn, bắt buộc dùng try...finally để đóng kết nối,
    ngăn chặn tràn RAM do Zombie Sessions trên OpenWrt.
    """
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh_client.connect(hostname=ip, username=username, password=password, timeout=5)
        _, stdout, stderr = ssh_client.exec_command(command)
        stdout.channel.recv_exit_status()  # Đợi lệnh thực thi hoàn tất trên Router
        if intent:
            logging.info(f"   [+] AP {ip}: Đã {intent}.")
        return True
    except Exception as e:
        logging.error(f"   [-] Lỗi SSH trên AP {ip}: {e}")
        return False
    finally:
        ssh_client.close()


def apply_ai_profile_atomic(ap_ip, username, password, radio_band, interface,
                            channel, bandwidth, tx_power_dbm, beacon_int, enable_bf=True):
    """
    Thực thi đồng bộ toàn bộ cấu hình của AI trong 1 phiên SSH duy nhất (Atomic Transaction)
    cho dòng chip MediaTek MT7981B (Xiaomi AX3000T - chuẩn IEEE 802.11ax).
    """
    bf_val = '1' if enable_bf else '0'
    iw_tx_mbm = int(tx_power_dbm) * 100

    uci_batch_cmd = (
        f"uci set wireless.{radio_band}.channel='{channel}' && "
        f"uci set wireless.{radio_band}.htmode='{bandwidth}' && "
        f"uci set wireless.{radio_band}.txpower='{int(tx_power_dbm)}' && "
        f"uci set wireless.{radio_band}.beacon_int='{int(beacon_int)}' && "
        f"uci set wireless.{radio_band}.he_bss_color='1' && "
        f"uci set wireless.{radio_band}.he_su_beamformer='{bf_val}' && "
        f"uci commit wireless && wifi reload && "
        f"sleep 2 && iw dev {interface} set txpower fixed {iw_tx_mbm}"
    )
    intent = (
        f"đồng bộ cấu hình AI trên {radio_band}/{interface}: "
        f"CH={channel}, BW={bandwidth}, Tx={tx_power_dbm}dBm, Beacon={beacon_int}ms"
    )
    return execute_ssh_command(ap_ip, username, password, uci_batch_cmd, intent)


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


def apply_mcs_ceiling(ap_ip, username, password, interface, mcs_limit=None):
    if isinstance(ap_ip, list):
        ap_ip = ap_ip[0]
    if mcs_limit is not None:
        command = f"iw dev {interface} set bitrates he-mcs-5 1:0-{mcs_limit} 2:0-{mcs_limit} 2>/dev/null || true"
        intent = f"Khóa trần HE-MCS ({interface}) ở mức {mcs_limit}"
    else:
        command = f"iw dev {interface} set bitrates"
        intent = f"Khôi phục Auto Rate ({interface})"
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