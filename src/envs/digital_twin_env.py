import gymnasium as gym
from gymnasium import spaces
import numpy as np
from typing import Any

class UltimateDigitalTwinEnv(gym.Env):
    """
    Môi trường Bản sao số Tối thượng (Digital Twin) - 8D Cross-Layer State & 7D Action.
    Tích hợp dữ liệu tầng Vật lý (PHY: Noise Floor, OFDMA Ratio) và tầng MAC (p_c, Retry, Airtime).

    Không gian trạng thái 8 chiều:
      [0] avg_rssi        : [-90.0, -30.0] dBm  (Cường độ tín hiệu thu)
      [1] noise_floor     : [-105.0, -50.0] dBm (Mức nhiễu nền vô tuyến - PHY)
      [2] sta_count       : [0.0, 300.0]        (Số lượng thiết bị kết nối)
      [3] retry_rate      : [0.0, 1.0]          (Tỷ lệ truyền lại khung tin)
      [4] p_c             : [0.0, 1.0]          (Xác suất va chạm tầng MAC)
      [5] airtime_util    : [0.0, 1.0]          (Mức chiếm dụng kênh truyền)
      [6] ofdma_ratio     : [0.0, 1.0]          (Tỷ lệ đa truy cập HE-MU OFDMA - PHY)
      [7] is_break_time   : [0.0, 1.0]          (Ngữ cảnh giờ ra chơi / bão tải)
    """
    def __init__(self, hardware_type="WIFI_6"):
        super().__init__()
        self.hardware_type = hardware_type
        self.action_space = spaces.MultiDiscrete([3, 3, 7, 12, 2, 5, 2])
        self.observation_space = spaces.Box(
            low=np.array([-90.0, -105.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32),
            high=np.array([-30.0, -50.0, 300.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        )
        self.current_step = 0
        self.max_steps = 300
        self.previous_action = None

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        self.current_step = 0
        self.previous_action = None
        # State mặc định bình yên: RSSI -65dBm, Noise -95dBm, 20 STAs, Retry 0.05, p_c 0.08, Airtime 0.35, OFDMA 0.70, Học
        return np.array([-65.0, -95.0, 20.0, 0.05, 0.08, 0.35, 0.70, 0.0], dtype=np.float32), {}

    def step(self, action):
        self.current_step += 1
        terminated = bool(self.current_step >= self.max_steps)
        return self.observation_space.sample(), 0.0, terminated, False, {}

    def simulate_future(self, current_real_state, action):
        """
        HÀM TIÊN TRI (WHAT-IF ENGINE):
        Phân biệt chính xác nguyên nhân mạng chậm:
          - Nhiễu vô tuyến ngoài RF (Noise Floor cao) -> Ưu tiên đổi kênh trực giao (CH40/44)
          - Nghẽn đụng độ CSMA/CA (p_c cao, Airtime cao, STA đông) -> Ưu tiên bóp HE20, điều chỉnh Beacon
        """
        ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_idx, bf_idx = action

        real_rssi = float(current_real_state[0])
        real_noise = float(current_real_state[1])
        actual_sta = float(current_real_state[2])
        real_retry = float(current_real_state[3])
        real_p_c = float(current_real_state[4])
        real_airtime = float(current_real_state[5])
        real_ofdma = float(current_real_state[6])
        is_break = int(current_real_state[7])

        # Đánh giá áp lực nghẽn mạng tầng MAC
        is_heavy_mac_congestion = (actual_sta > 80) or (real_airtime > 0.72) or (real_p_c > 0.35) or (real_retry > 0.35)
        # Đánh giá nhiễu vật lý tầng PHY (Noise Floor ô nhiễm > -78 dBm)
        is_rf_interference = (real_noise > -78.0)

        # 1. Mô phỏng chọn Kênh dựa trên Nhiễu RF và Va chạm MAC
        if is_rf_interference or real_p_c >= 0.75:
            # Nhiễu nặng cả RF hoặc bão va chạm: Kênh 36 bị đè bẹp -> Sơ tán lên Kênh 44 (idx 2)
            ch_noise = 0.65 if ch_idx == 0 else (0.50 if ch_idx == 1 else 0.04)
        elif real_p_c >= 0.25 or is_heavy_mac_congestion or real_noise > -84.0:
            # Nhiễu vừa: Kênh 36 có rủi ro -> Sơ tán sang Kênh 40 (idx 1)
            ch_noise = 0.48 if ch_idx == 0 else (0.04 if ch_idx == 1 else 0.08)
        else:
            # Kênh sạch (Noise Floor < -88dBm, p_c thấp): Giữ nguyên Kênh 36 là tối ưu
            ch_noise = 0.03 if ch_idx == 0 else (0.12 if ch_idx == 1 else 0.16)

        # 2. Mô phỏng chọn Băng thông
        if is_heavy_mac_congestion:
            # Bão tải / Đông người: HE20 (idx 0) triệt tiêu nghẽn và tăng SNR per tone
            base_capacity = 140.0 if bw_idx == 0 else (70.0 if bw_idx == 1 else 35.0)
            bw_penalty = 0.0 if bw_idx == 0 else (0.25 if bw_idx == 1 else 0.50)
        else:
            # Mạng thoáng: Mở HE80 (idx 2) để tối đa throughput
            base_capacity = 60.0 if bw_idx == 0 else (120.0 if bw_idx == 1 else 200.0)
            bw_penalty = 0.08 if bw_idx == 0 else (0.03 if bw_idx == 1 else 0.0)

        # 3. Phạt / Thưởng bổ trợ
        congestion_stress = max(actual_sta / 150.0, real_p_c * 1.2, real_airtime * 0.9, real_retry * 1.1)
        congestion_stress = float(np.clip(congestion_stress, 0.05, 1.0))
        mcs_bonus = -0.04 if (congestion_stress > 0.55 and 6 <= mcs_idx <= 9) or (congestion_stress <= 0.55 and mcs_idx >= 9) else 0.03
        ax_bonus = (-0.03 if bf_idx == 1 else 0.02) + (-0.02 if 1 <= obss_idx <= 3 else 0.01)

        # Thưởng nếu tận dụng tốt OFDMA
        ofdma_bonus = 0.05 if (real_ofdma > 0.50 and bw_idx <= 1) else 0.0

        # Tính toán Reward dự đoán
        pred_pc = float(np.clip(ch_noise + bw_penalty + mcs_bonus + ax_bonus, 0.01, 0.99))
        pred_tput = base_capacity * (1.0 - pred_pc)

        norm_throughput = pred_tput / 100.0
        penalty_collision = 4.0 * (pred_pc ** 2)
        beacon_bonus = 0.10 if ((is_break == 1 and beacon_idx == 0) or (is_break == 0 and beacon_idx == 1)) else 0.0

        action_penalty = 0.0
        if self.previous_action is not None:
            if ch_idx != self.previous_action[0]:
                action_penalty -= 0.35
            if bw_idx != self.previous_action[1]:
                action_penalty -= 0.20

        return float(norm_throughput - penalty_collision + beacon_bonus + ofdma_bonus + action_penalty)
