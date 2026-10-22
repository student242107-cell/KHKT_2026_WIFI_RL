import os
import csv
from datetime import datetime

class AILogger:
    """Hộp đen ghi nhận quyết định của AI và trạng thái mạng tương ứng."""
    def __init__(self, log_path="data/processed/ai_actions_log.csv"):
        self.log_path = log_path
        self.headers = [
            "Timestamp", "AP_IP", "BSSID", "STA_Count", "RSSI", "p_c", "Airtime_Util",
            "CH_Idx", "BW_Idx", "TxPower_Idx", "MCS_Idx", "Beacon_Idx",
            "Expected_Reward", "Status"
        ]
        self._ensure_log_file()

    def _ensure_log_file(self):
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if not os.path.exists(self.log_path):
            with open(self.log_path, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(self.headers)

    def log_action(self, ap_ip, bssid, state_arr, action_arr, expected_reward, status):
        """Ghi log quyết định. Các Hotspot sẽ được nhóm theo thời gian (Timestamp)."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sta_count = int(state_arr[4])
        rssi = float(state_arr[0])
        p_c = float(state_arr[1])
        airtime = float(state_arr[3])
        
        ch_idx = int(action_arr[0])
        bw_idx = int(action_arr[1])
        tx_idx = int(action_arr[2])
        mcs_idx = int(action_arr[3])
        beacon_idx = int(action_arr[4])

        with open(self.log_path, mode="a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                now_str, ap_ip, bssid, sta_count, f"{rssi:.1f}", f"{p_c:.3f}", f"{airtime:.3f}",
                ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx,
                f"{expected_reward:.2f}", status
            ])