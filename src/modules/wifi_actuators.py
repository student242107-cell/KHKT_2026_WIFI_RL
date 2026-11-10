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


def compute_adaptive_aql(bandwidth="HE20", p_c=0.0, airtime_util=0.0, sta_count=1):
    """
    Tính toán Dynamic Adaptive AQL (Airtime Queue Limit):
    - Khi bão UDP Jammer hoặc nghẽn mạng nặng (p_c > 0.20 hoặc airtime_util > 0.50 hoặc STAs >= 80):
      Ép hàng đợi xuống mức phòng thủ 1200us để triệt tiêu Bufferbloat,
      đảm bảo cam kết độ trễ đuôi P99 luôn < 15ms.
    - Khi mạng tải trung bình (HE20): 1600us.
    - Khi mạng tải trung bình (HE40): 2400us.
    - Khi mạng thoáng, cần throughput tối đa (HE80, ít thiết bị): 3200us (để gom đủ A-MPDU aggregation).
    """
    try:
        is_congestion = (float(p_c) > 0.20) or (float(airtime_util) > 0.50) or (int(sta_count) >= 80)
        if is_congestion:
            return 1200
        if bandwidth == "HE20":
            return 1600
        elif bandwidth == "HE40":
            return 2400
        else:  # HE80 hoặc cao hơn
            return 3200
    except Exception as e:
        logging.warning(f"[AQL] Lỗi tính toán adaptive AQL: {e}. Fallback về 1600us.")
        return 1600


def apply_ai_profile_atomic(
    ap_ip, username, password,
    radio_band="radio1", interface="phy1-ap0",
    channel=44, bandwidth="HE20", tx_power_dbm=18,
    beacon_int=80, enable_bf=True,
    aql_limit=None, enable_atf=True,
    obss_pd_offset=None
):
    """
    Thực thi nguyên tử (Atomic) kết hợp BỘ BA VŨ KHÍ TẦNG MAC:
      1. Airtime Fairness (ATF): Phân bổ thời gian công bằng, triệt tiêu client yếu làm chậm mạng.
      2. Dynamic Adaptive AQL: Giới hạn thời gian hàng đợi driver mt76 (1200us khi bão tải).
      3. FQ-CoDel AQM: Tách luồng công bằng, chủ động drop gói UDP vượt ngưỡng target 5ms.
    Cơ chế áp dụng 7 chiều action:
      - Nhóm "cần reload" (channel, htmode, beacon_int, he_su_beamformer, OBSS-PD): chỉ `wifi reload`
        khi ít nhất một giá trị THỰC SỰ thay đổi so với UCI hiện tại.
      - Nhóm "áp nóng" (txpower): áp ngay bằng `iw ... set txpower fixed` không làm rớt kết nối.
    """
    try:
        bf_val = 1 if enable_bf else 0
        phy_idx = "phy1" if "1" in radio_band or "1" in interface else "phy0"
        tx_mbm = int(round(float(tx_power_dbm) * 100))

        # Nếu aql_limit chưa được chỉ định, tính theo băng thông
        if aql_limit is None:
            aql_limit = 1600 if bandwidth == "HE20" else (2400 if bandwidth == "HE40" else 3200)

        # OBSS-PD Spatial Reuse (Wi-Fi 6): he_spr_sr_control=4 -> bật Non-SRG OBSS PD offset
        if obss_pd_offset is not None:
            obss_off = int(max(0, min(20, int(obss_pd_offset))))
            obss_get = f"CUR_OBSS=$(uci -q get wireless.{radio_band}.he_spr_non_srg_obss_pd_max_offset); "
            obss_set = (f"uci set wireless.{radio_band}.he_spr_sr_control='4'; "
                        f"uci set wireless.{radio_band}.he_spr_non_srg_obss_pd_max_offset='{obss_off}'; ")
            obss_cmp = f" || [ \"$CUR_OBSS\" != \"{obss_off}\" ]"
        else:
            obss_get, obss_set, obss_cmp = "", "", ""

        cmd = (
            # 1. Đọc cấu hình hiện tại để quyết định có cần reload hay không
            f"CUR_CH=$(uci -q get wireless.{radio_band}.channel); "
            f"CUR_BW=$(uci -q get wireless.{radio_band}.htmode); "
            f"CUR_BCN=$(uci -q get wireless.{radio_band}.beacon_int); "
            f"CUR_BF=$(uci -q get wireless.{radio_band}.he_su_beamformer); "
            f"{obss_get}"
            # 2. Cấu hình UCI
            f"uci set wireless.{radio_band}.channel='{channel}'; "
            f"uci set wireless.{radio_band}.htmode='{bandwidth}'; "
            f"uci set wireless.{radio_band}.txpower='{tx_power_dbm}'; "
            f"uci set wireless.{radio_band}.beacon_int='{beacon_int}'; "
            f"uci set wireless.{radio_band}.he_bss_color='8'; "
            f"uci set wireless.{radio_band}.he_su_beamformer='{bf_val}'; "
            f"{obss_set}"
            f"uci set wireless.{radio_band}.airtime_fairness='1' 2>/dev/null || true; "
            f"uci set wireless.default_{radio_band}.airtime_fairness='1' 2>/dev/null || true; "
            f"uci commit wireless; "
            f"if [ \"$CUR_CH\" != \"{channel}\" ] || [ \"$CUR_BW\" != \"{bandwidth}\" ] "
            f"|| [ \"$CUR_BCN\" != \"{beacon_int}\" ] || [ \"$CUR_BF\" != \"{bf_val}\" ]{obss_cmp}; then "
            f"  wifi reload; sleep 2; "
            f"fi; "
            # 3. Áp nóng công suất phát (không cần reload)
            f"iw dev {interface} set txpower fixed {tx_mbm} 2>/dev/null || true; "
            # 2. VŨ KHÍ 1: Airtime Fairness (ATF) kernel debugfs
            f"[ -f /sys/kernel/debug/ieee80211/{phy_idx}/airtime_fairness ] && echo 1 > /sys/kernel/debug/ieee80211/{phy_idx}/airtime_fairness 2>/dev/null || true; "
            # 3. VŨ KHÍ 2: AQL (Airtime Queue Limit) thích ứng động
            f"iw phy {phy_idx} set txq limit 2048 2>/dev/null || true; "
            f"if [ -d /sys/kernel/debug/ieee80211/{phy_idx} ]; then "
            f"  echo {aql_limit} > /sys/kernel/debug/ieee80211/{phy_idx}/aql_txq_limit 2>/dev/null || true; "
            f"  echo \"{aql_limit} {aql_limit}\" > /sys/kernel/debug/ieee80211/{phy_idx}/aql_threshold 2>/dev/null || true; "
            f"fi; "
            # 4. VŨ KHÍ 3: FQ-CoDel AQM cắt tỉa bão UDP tại nguồn (target 5ms)
            f"if which tc >/dev/null 2>&1; then "
            f"  for dev in $(ls /sys/class/net/ 2>/dev/null | grep -E '^({phy_idx}-ap|wlan1|wlan0)'); do "
            f"    tc qdisc replace dev $dev root fq_codel limit 2048 target 5ms interval 50ms ecn 2>/dev/null || true; "
            f"  done; "
            f"  tc qdisc replace dev br-lan root fq_codel limit 2048 target 5ms interval 50ms ecn 2>/dev/null || true; "
            f"fi"
        )
        success = _exec_ssh_command(ap_ip, username, password, cmd)
        if success:
            logging.info(
                f"   [+] AP {ap_ip}: Đã đồng bộ AI Profile ({radio_band}): "
                f"CH={channel}, BW={bandwidth}, Tx={tx_power_dbm}dBm, Beacon={beacon_int}ms, "
                f"BF={'ON' if bf_val else 'OFF'}, OBSS-PD offset={obss_pd_offset} | "
                f"🛡️ MAC Guard: ATF=BẬT, AQL={aql_limit}us, FQ-CoDel=target 5ms."
            )
        return success
    except Exception as e:
        logging.error(f"[-] Ngoại lệ trong apply_ai_profile_atomic ({ap_ip}): {e}")
        return False


def apply_mcs_ceiling(ap_ip, username, password, interface="phy1-ap0", mcs_index=7):
    """
    Khóa trần HE-MCS (chống Minstrel dò MCS quá cao khi kênh fading/nhiễu) — áp nóng, không reload.
      - mcs_index >= 11: gỡ giới hạn (trả quyền cho rate control mặc định).
      - Ngược lại: `iw dev <if> set bitrates he-mcs-5|he-mcs-2.4 1:0-N 2:0-N`.
    Driver/iw không hỗ trợ -> bỏ qua an toàn, không làm sập agent.
    """
    try:
        mcs = int(max(0, min(11, int(mcs_index))))
        band_key = "he-mcs-2.4" if ("phy0" in interface or interface.startswith("wlan0")) else "he-mcs-5"
        if mcs >= 11:
            cmd = f"iw dev {interface} set bitrates 2>/dev/null || true"
        else:
            cmd = (f"iw dev {interface} set bitrates {band_key} 1:0-{mcs} 2:0-{mcs} 2>/dev/null "
                   f"|| iw dev {interface} set bitrates 2>/dev/null || true")
        return _exec_ssh_command(ap_ip, username, password, cmd)
    except Exception as e:
        logging.warning(f"[MCS] Không áp được trần MCS cho {ap_ip}: {e}")
        return False


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