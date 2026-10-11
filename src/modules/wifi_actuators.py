import paramiko
import time
import logging

# ==============================================================================
# BỘ CÔNG CỤ ĐIỀU KHIỂN ROUTER OPENWRT BẰNG AI (WIFI ACTUATORS)
# Hỗ trợ Kiến trúc Multi-AP và Dual-Band (2.4GHz / 5GHz)
# ==============================================================================

def execute_ssh_command(ip, username, password, command, intent=""):
    """
    Hàm lõi để thực thi SSH an toàn, bắt buộc dùng try...finally để đóng kết nối,
    ngăn chặn tràn RAM do Zombie Sessions trên OpenWrt.
    """
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh_client.connect(hostname=ip, username=username, password=password, timeout=5)
        ssh_client.exec_command(command)
        if intent:
            logging.info(f"   [+] AP {ip}: Đã {intent}.")
    except Exception as e:
        logging.error(f"   [-] Lỗi SSH trên AP {ip}: {e}")
    finally:
        ssh_client.close()

# ------------------------------------------------------------------------------
# CHIẾN THUẬT 2: TX POWER MATCHING (Động lực học công suất)
# ------------------------------------------------------------------------------
def optimize_tx_power(ap_ip, username, password, interface, target_power_dbm):
    """
    Điều chỉnh Tx Power qua lệnh 'iw' (Tầng vật lý) để tránh Restart mạng.
    - interface: 'wlan0' (5GHz) hoặc 'wlan1' (2.4GHz)
    """
    iw_val = target_power_dbm * 100 # Chuyển đổi dBm sang mBm cho iw
    command = f"iw dev {interface} set txpower fixed {iw_val}"
    intent = f"bóp công suất ({interface}) xuống {target_power_dbm}dBm để triệt tiêu CCI"
    execute_ssh_command(ap_ip, username, password, command, intent)

# ------------------------------------------------------------------------------
# CHIẾN THUẬT 4: THAO TÚNG BEACON INTERVAL ĐỂ HỖ TRỢ ROAMING
# ------------------------------------------------------------------------------
def adjust_beacon_interval(ap_ip, username, password, radio_band, interval_ms):
    """
    Giãn Beacon (300ms) để tiết kiệm Airtime hoặc Rút ngắn (80ms) để quét Roaming nhanh.
    - radio_band: 'radio0' (5GHz) hoặc 'radio1' (2.4GHz)
    - ap_ip: IP đích của AP cần cấu hình. Nếu là mảng (list), tự động xử lý.
    """
    # Nếu truyền vào là mảng (Legacy mode), lấy phần tử đầu tiên
    if isinstance(ap_ip, list): 
        ap_ip = ap_ip[0]
        
    command = f"uci set wireless.{radio_band}.beacon_int='{interval_ms}' && uci commit wireless && wifi reload"
    intent = f"đổi chu kỳ Beacon ({radio_band}) thành {interval_ms}ms"
    execute_ssh_command(ap_ip, username, password, command, intent)

# ------------------------------------------------------------------------------
# CHIẾN THUẬT 5.1: KHÓA TRẦN TỐC ĐỘ (MCS CEILING) CHỐNG NHIỄU ĐA ĐƯỜNG
# ------------------------------------------------------------------------------
def apply_mcs_ceiling(ap_ip, username, password, interface, mcs_limit=None):
    """
    Khóa trần tốc độ. Dùng iw để can thiệp Zero-downtime.
    - interface: 'wlan0' hoặc 'wlan1'
    """
    if isinstance(ap_ip, list):
        ap_ip = ap_ip[0]
        
    if mcs_limit is not None:
        # 1:0-X và 2:0-X là khóa cho 2 luồng ăng-ten MIMO
        command = f"iw dev {interface} set bitrates vht-mcs-5 1:0-{mcs_limit} 2:0-{mcs_limit}"
        intent = f"Khóa trần MCS ({interface}) ở mức {mcs_limit}"
    else:
        command = f"iw dev {interface} set bitrates" # Trả về thuật toán Rate Adaptation của Linux
        intent = f"Khôi phục Auto Rate ({interface})"
        
    execute_ssh_command(ap_ip, username, password, command, intent)

# ------------------------------------------------------------------------------
# CHIẾN THUẬT 6: BANDWIDTH ELASTICITY & FALLBACK (Co giãn băng thông)
# ------------------------------------------------------------------------------
def adaptive_bandwidth_fallback(ap_ip, username, password, radio_band, bandwidth):
    """
    Giảm băng thông (Fallback) từ 80MHz xuống 40MHz/20MHz khi bị nhiễu nặng hoặc bão sinh viên.
    """
    # Lệnh ghép: Thiết lập độ rộng kênh và reload để áp dụng
    command = f"uci set wireless.{radio_band}.htmode='{bandwidth}' && uci commit wireless && wifi reload"
    intent = f"ép băng thông ({radio_band}) chuyển về {bandwidth}"
    execute_ssh_command(ap_ip, username, password, command, intent)

# ------------------------------------------------------------------------------
# BỔ SUNG CHIẾN THUẬT: ĐIỀU CHỈNH BÚP SÓNG THÔNG MINH (ADAPTIVE BEAMFORMING)
# ------------------------------------------------------------------------------
def ai_action_toggle_beamforming(ap_ip, username, password, radio_band, enable_bf=True):
    """
    AI tự động tắt Beamforming nếu phát hiện học sinh di chuyển quá nhanh (Stale CSI),
    và bật lại khi lớp học ngồi im.
    - radio_band: OpenWrt quản lý Beamforming ở cấp độ radio phần cứng.
    """
    bf_value = '1' if enable_bf else '0'
    action_name = 'BẬT' if enable_bf else 'TẮT'
    
    # Kích hoạt cả VHT (Wi-Fi 5) và HE (Wi-Fi 6) Beamformer
    uci_commands = (
        f"uci set wireless.{radio_band}.vht_beamform='{bf_value}' && "
        f"uci set wireless.{radio_band}.he_beamform='{bf_value}' && "
        "uci commit wireless && wifi reload"
    )
    intent = f"{action_name} chùm sóng Beamforming trên {radio_band}"
    execute_ssh_command(ap_ip, username, password, uci_commands, intent)

# ------------------------------------------------------------------------------
# CHIẾN THUẬT 3.1: CONTEXT-AWARE SSID PRUNING (Cắt tỉa mạng rác)
# ------------------------------------------------------------------------------
def prune_unused_ssid(ap_ip, username, password, vap_interface, disable=True):
    """
    Tắt các SSID ảo không có người dùng để giải phóng Airtime (Overhead beacon).
    - vap_interface: Giao diện ảo được cấu hình trong OpenWrt (VD: 'teacher_radio0')
    """
    action_val = '1' if disable else '0'
    action_name = 'TẮT' if disable else 'BẬT'
    
    command = f"uci set wireless.{vap_interface}.disabled='{action_val}' && uci commit wireless && wifi reload"
    intent = f"{action_name} mạng ảo {vap_interface}"
    execute_ssh_command(ap_ip, username, password, command, intent)