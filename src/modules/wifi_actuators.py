import paramiko
import logging
import re

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


def _exec_ssh_output(ap_ip, username, password, command):
    """Thực thi lệnh SSH và trả về chuỗi stdout."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ap_ip, username=username, password=password, timeout=6)
        _, stdout, _ = ssh.exec_command(command)
        return stdout.read().decode("utf-8", errors="ignore").strip()
    except Exception as e:
        logging.error(f"[-] Lỗi đọc SSH từ {ap_ip}: {e}")
        return ""
    finally:
        ssh.close()


def apply_ai_profile_atomic(
    ap_ip, username, password,
    radio_band="radio1", interface="phy1-ap0",
    channel=44, bandwidth="HE20", tx_power_dbm=18,
    beacon_int=80, enable_bf=True
):
    """
    Thực thi nguyên tử (Atomic) cho cả mạng Student và Teacher trên cùng Radio:
    Đợi 2 giây sau 'wifi reload' rồi áp dụng AQL + FQ-CoDel lên mọi Virtual Interface (phy1-ap*).
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
        f"for dev in $(ls /sys/class/net/ | grep '^{phy_idx}-ap'); do "
        f"  tc qdisc replace dev $dev root fq_codel limit 2048 target 5ms interval 50ms ecn 2>/dev/null; "
        f"done; "
        f"tc qdisc replace dev br-lan root fq_codel limit 2048 target 5ms interval 50ms ecn 2>/dev/null"
    )
    success = _exec_ssh_command(ap_ip, username, password, cmd)
    if success:
        logging.info(
            f"   [+] AP {ap_ip}: Đã đồng bộ AI Profile ({radio_band}): "
            f"CH={channel}, BW={bandwidth}, Tx={tx_power_dbm}dBm, Beacon={beacon_int}ms + AQL/FQ-CoDel."
        )
    return success


def apply_mcs_ceiling(ap_ip, username, password, interface="phy1-ap0", mcs_index=7):
    return True


def get_teacher_active_clients(ap_ip, username, password, teacher_ssid="School_Teacher"):
    """
    THƯỢNG TÔN TRẢI NGHIỆM NGƯỜI DÙNG:
    Kiểm tra trực tiếp trên AP xem giao diện phát SSID 'School_Teacher' hiện có bao nhiêu
    thiết bị đang kết nối VÀ trả về danh sách MAC của các thiết bị đó.
    """
    cmd = (
        "for dev in $(iw dev | awk '$1==\"Interface\"{print $2}'); do "
        f"  ssid=$(iw dev $dev info | awk '$1==\"ssid\"{{print $2}}'); "
        f"  if [ \"$ssid\" = \"{teacher_ssid}\" ]; then "
        "    iw dev $dev station dump | awk '$1==\"Station\"{print toupper($2)}'; "
        "  fi; "
        "done"
    )
    out = _exec_ssh_output(ap_ip, username, password, cmd)
    macs = [line.strip() for line in out.splitlines() if re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", line.strip())]
    return macs


def prune_unused_ssid(ap_ip, username, password, vap_section="teacher_5g", disable=True):
    """
    Tính năng Dynamic SSID Pruning (Tỉa SSID Động):
    - Nếu disable=False: Đánh thức mạng 'teacher_5g' (School_Teacher) ngay lập tức.
    - Nếu disable=True: Kiểm tra xem còn bất kỳ Giáo viên nào đang kết nối không.
      Nếu còn >= 1 thiết bị đang kết nối, TUYỆT ĐỐI KHÔNG TẮT!
    """
    if disable:
        active_macs = get_teacher_active_clients(ap_ip, username, password)
        if len(active_macs) > 0:
            logging.info(
                f"   [🛡️ USER-CENTRIC GUARD] Phát hiện {len(active_macs)} thiết bị GV ({active_macs}) "
                f"vẫn đang kết nối tại AP {ap_ip}. TUYỆT ĐỐI KHÔNG TẮT mạng School_Teacher!"
            )
            return False

    val = '1' if disable else '0'
    state_str = "TẮT (Thu hồi Airtime)" if disable else "BẬT (Đón Giáo viên VIP)"
    cmd = (
        f"CUR=$(uci -q get wireless.{vap_section}.disabled); "
        f"if [ \"$CUR\" != \"{val}\" ]; then "
        f"  uci set wireless.{vap_section}.disabled='{val}' && uci commit wireless && wifi reload; "
        f"fi"
    )
    success = _exec_ssh_command(ap_ip, username, password, cmd)
    if success:
        logging.info(f"   [+] AP {ap_ip}: Đã {state_str} giao diện '{vap_section}'.")
    return success


def optimize_tx_power(ap_ip, username, password, radio_band="radio1", target_power_dbm=20):
    cmd = f"uci set wireless.{radio_band}.txpower='{target_power_dbm}' && uci commit wireless && wifi reload"
    return _exec_ssh_command(ap_ip, username, password, cmd)


def adjust_beacon_interval(ap_ip, username, password, radio_band="radio1", interval=100):
    cmd = f"uci set wireless.{radio_band}.beacon_int='{interval}' && uci commit wireless && wifi reload"
    return _exec_ssh_command(ap_ip, username, password, cmd)


def adaptive_bandwidth_fallback(ap_ip, username, password, radio_band="radio1", htmode="HE20"):
    cmd = f"uci set wireless.{radio_band}.htmode='{htmode}' && uci commit wireless && wifi reload"
    return _exec_ssh_command(ap_ip, username, password, cmd)


def ai_action_toggle_beamforming(ap_ip, username, password, radio_band="radio1", enable=True):
    val = '1' if enable else '0'
    cmd = f"uci set wireless.{radio_band}.he_su_beamformer='{val}' && uci commit wireless && wifi reload"
    return _exec_ssh_command(ap_ip, username, password, cmd)