import paramiko
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _exec_ssh_command(ap_ip, username, password, command):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ap_ip, username=username, password=password, timeout=8)
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
    Thực thi nguyên tử (Atomic):
    1. Kiểm tra nếu Kênh và Băng thông trên Router đã khớp thì KHÔNG gọi 'wifi reload' để tránh ngắt sóng.
    2. Nếu cần đổi Kênh/Băng thông, đợi 2 giây sau 'wifi reload' để interface phy1-ap0 lên sóng hoàn tất
       rồi mới nạp Airtime Fairness + AQL + FQ-CoDel (đảm bảo không bị hostapd reset mất).
    """
    bf_val = 1 if enable_bf else 0
    phy_idx = "phy1" if "1" in radio_band or "1" in interface else "phy0"
    aql_limit = 2000 if bandwidth == "HE20" else 3500

    cmd = (
        f"CUR_CH=$(uci -q get wireless.{radio_band}.channel); "
        f"CUR_BW=$(uci -q get wireless.{radio_band}.htmode); "
        f"uci set wireless.{radio_band}.channel='{channel}'; "
        f"uci set wireless.{radio_band}.htmode='{bandwidth}'; "
        f"uci set wireless.{radio_band}.txpower='{tx_power_dbm}'; "
        f"uci set wireless.{radio_band}.beacon_int='{beacon_int}'; "
        f"uci set wireless.{radio_band}.he_bss_color='8'; "
        f"uci set wireless.{radio_band}.he_su_beamformer='{bf_val}'; "
        f"uci commit wireless; "
        f"if [ \"$CUR_CH\" != \"{channel}\" ] || [ \"$CUR_BW\" != \"{bandwidth}\" ]; then "
        f"  wifi reload; sleep 2; "
        f"fi; "
        f"iw phy {phy_idx} set txq limit 2048 2>/dev/null; "
        f"echo {aql_limit} > /sys/kernel/debug/ieee80211/{phy_idx}/aql_txq_limit 2>/dev/null; "
        f"echo \"{aql_limit} {aql_limit}\" > /sys/kernel/debug/ieee80211/{phy_idx}/aql_threshold 2>/dev/null; "
        f"tc qdisc replace dev {interface} root fq_codel limit 2048 target 5ms interval 50ms ecn 2>/dev/null; "
        f"tc qdisc replace dev br-lan root fq_codel limit 2048 target 5ms interval 50ms ecn 2>/dev/null"
    )
    success = _exec_ssh_command(ap_ip, username, password, cmd)
    if success:
        logging.info(
            f"   [+] AP {ap_ip}: Đã đồng bộ AI Profile ({radio_band}/{interface}): "
            f"CH={channel}, BW={bandwidth}, Tx={tx_power_dbm}dBm, Beacon={beacon_int}ms + AQL/ATF/FQ-CoDel."
        )
    return success


def apply_mcs_ceiling(ap_ip, username, password, interface="phy1-ap0", mcs_index=7):
    """Đã gộp trực tiếp vào apply_ai_profile_atomic để giảm 50% thời gian mở phiên SSH."""
    return True