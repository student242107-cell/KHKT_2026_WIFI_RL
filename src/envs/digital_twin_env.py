import gymnasium as gym
from gymnasium import spaces
import numpy as np
from typing import Any

class UltimateDigitalTwinEnv(gym.Env):
    """
    Môi trường Bản sao số Tối thượng (Digital Twin).
    Đồng bộ logic vật lý 100% với Colab để chấm điểm Oracle chính xác, tránh Veto oan uổng.
    """
    def __init__(self, hardware_type="WIFI_6"):
        super().__init__()
        self.hardware_type = hardware_type
        self.action_space = spaces.MultiDiscrete([3, 3, 7, 12, 2, 5, 2])
        self.observation_space = spaces.Box(
            low=np.array([-90.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32),
            high=np.array([-30.0, 1.0, 1.0, 1.0, 300.0, 23.0, 59.0, 1.0], dtype=np.float32)
        )
        self.current_step = 0
        self.max_steps = 300
        self.previous_action = None

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        self.current_step = 0
        self.previous_action = None
        return np.array([-70.0, 0.1, 0.05, 0.4, 20.0, 8.0, 15.0, 0.0], dtype=np.float32), {}

    def step(self, action):
        # Môi trường này chỉ dùng để Oracle gọi simulate_future, hàm step này để bypass
        self.current_step += 1
        terminated = bool(self.current_step >= self.max_steps)
        return self.observation_space.sample(), 0.0, terminated, False, {}

    def simulate_future(self, current_real_state, action):
        """
        HÀM TIÊN TRI (WHAT-IF ENGINE):
        Đồng bộ với Colab, phân loại mức độ nghẽn mạng để chấm điểm khách quan.
        Tránh phạt oan cấu hình tốt khi mạng đang nghẽn do yếu tố khách quan (UDP Jammer).
        """
        ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_idx, bf_idx = action

        real_p_c = float(current_real_state[1])
        real_retry = float(current_real_state[2])
        real_airtime = float(current_real_state[3])
        actual_sta = float(current_real_state[4])
        is_break = int(current_real_state[7])

        # Đánh giá áp lực nghẽn mạng (Bão STAs hoặc Bão UDP)
        congestion_stress = max(actual_sta / 150.0, real_p_c * 1.2, real_airtime * 0.9, real_retry * 1.1)
        congestion_stress = float(np.clip(congestion_stress, 0.05, 1.0))
        is_heavy_congestion = (actual_sta > 80) or (real_airtime > 0.72) or (real_retry > 0.35)

        # Mô phỏng chọn Kênh
        if real_p_c >= 0.75:
            ch_noise = 0.65 if ch_idx == 0 else (0.58 if ch_idx == 1 else 0.04) # Sơ tán lên 44 (idx 2)
        elif real_p_c >= 0.25 or is_heavy_congestion:
            ch_noise = 0.50 if ch_idx == 0 else (0.04 if ch_idx == 1 else 0.06) # Sơ tán lên 40/44
        else:
            ch_noise = 0.03 if ch_idx == 0 else (0.14 if ch_idx == 1 else 0.18) # Giữ nguyên 36

        # Mô phỏng chọn Băng thông
        if is_heavy_congestion:
            base_capacity = 140.0 if bw_idx == 0 else (70.0 if bw_idx == 1 else 35.0) # Bóp HE20 (idx 0)
            bw_penalty = 0.0 if bw_idx == 0 else (0.25 if bw_idx == 1 else 0.50)
        else:
            base_capacity = 60.0 if bw_idx == 0 else (120.0 if bw_idx == 1 else 200.0) # Mở HE80 (idx 2)
            bw_penalty = 0.08 if bw_idx == 0 else (0.03 if bw_idx == 1 else 0.0)

        mcs_bonus = -0.04 if (congestion_stress > 0.55 and 6 <= mcs_idx <= 9) or (congestion_stress <= 0.55 and mcs_idx >= 9) else 0.03
        ax_bonus = (-0.03 if bf_idx == 1 else 0.02) + (-0.02 if 1 <= obss_idx <= 3 else 0.01)

        # Tính toán Reward dự đoán
        pred_pc = float(np.clip(ch_noise + bw_penalty + mcs_bonus + ax_bonus, 0.01, 0.99))
        pred_tput = base_capacity * (1.0 - pred_pc)

        norm_throughput = pred_tput / 100.0
        penalty_collision = 4.0 * (pred_pc ** 2)  # Đồng bộ công thức phạt (hệ số 4.0)
        beacon_bonus = 0.10 if ((is_break == 1 and beacon_idx == 0) or (is_break == 0 and beacon_idx == 1)) else 0.0

        action_penalty = 0.0
        if self.previous_action is not None:
            if ch_idx != self.previous_action[0]:
                action_penalty -= 0.35
            if bw_idx != self.previous_action[1]:
                action_penalty -= 0.20

        return float(norm_throughput - penalty_collision + beacon_bonus + action_penalty)