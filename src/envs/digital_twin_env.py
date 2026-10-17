import gymnasium as gym
from gymnasium import spaces
import numpy as np
from typing import Any


class UltimateDigitalTwinEnv(gym.Env):
    """
    Môi trường Bản sao số Tối thượng (Digital Twin).
    Áp dụng thuật toán phạt phi tuyến tính (Exponential Penalty) và Chaos Engineering.
    """
    def __init__(self, hardware_type="WIFI_6"):
        super().__init__()
        # Action: [Channel(3), BW(3), TxPower(7), MCS(12), Beacon(2), OBSS_PD(5), Beamform(2)]
        self.action_space = spaces.MultiDiscrete([3, 3, 7, 12, 2, 5, 2])
        # State: [RSSI, p_c, Retry_Rate, Airtime_Util, STA_Count, Hour, Minute, Is_Break_Time]
        self.observation_space = spaces.Box(
            low=np.array([-90.0, 0.0, 0.0, 0.0, 0, 0, 0, 0], dtype=np.float32),
            high=np.array([-30.0, 1.0, 1.0, 1.0, 300, 23, 59, 1], dtype=np.float32)
        )
        self.current_step = 0
        self.max_steps = 300
        self.previous_action = None
        self.disaster_timer = 0
        self.disaster_type = 0

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        self.current_step = 0
        self.previous_action = None
        self.disaster_timer = 0
        self.disaster_type = 0
        return np.array([-70.0, 0.1, 0.05, 0.4, 20, 8, 15, 0], dtype=np.float32), {}

    def step(self, action):
        self.current_step += 1
        channel_idx, bw_idx, tx_power_idx = action[0], action[1], action[2]

        if self.disaster_timer <= 0:
            if np.random.rand() < 0.15:
                self.disaster_timer = np.random.randint(20, 50)
                self.disaster_type = np.random.choice([1, 2])
            else:
                self.disaster_type = 0
        else:
            self.disaster_timer -= 1

        noise_level = 0.1 if channel_idx == 0 else (0.2 if channel_idx == 1 else 0.4)
        actual_sta = np.random.randint(15, 35)

        if self.disaster_type == 1 and channel_idx == 0:
            noise_level = 0.95
        elif self.disaster_type == 2:
            actual_sta = np.random.randint(120, 180)

        base_throughput = 50.0 if bw_idx == 0 else (100.0 if bw_idx == 1 else 200.0)
        p_c = noise_level + (actual_sta * 0.005)

        if bw_idx == 2 and actual_sta > 100:
            p_c += 0.5
        elif bw_idx == 1 and actual_sta > 100:
            p_c += 0.2

        p_c = min(1.0, p_c)
        actual_throughput = base_throughput * (1.0 - p_c)

        norm_throughput = actual_throughput / 100.0
        penalty_collision = 5.0 * (p_c ** 2)
        base_reward = norm_throughput - penalty_collision

        action_penalty = 0.0
        if self.previous_action is not None:
            if channel_idx != self.previous_action[0]:
                action_penalty -= 0.5
            if bw_idx != self.previous_action[1]:
                action_penalty -= 0.3

        self.previous_action = action.copy()
        final_reward = base_reward + action_penalty

        state = np.array([-65.0, p_c, 0.05, 0.4, actual_sta, 8, 15, 0], dtype=np.float32)
        terminated = bool(self.current_step >= self.max_steps)

        return state, float(final_reward), terminated, False, {}

    def simulate_future(self, current_real_state, action):
        """
        HÀM TIÊN TRI (WHAT-IF ENGINE):
        Tích hợp cơ chế Quy đổi Tải Ảo (Effective Virtual Load) để nhận diện chính xác
        cả bão sinh viên (>100 máy) lẫn bão UDP Jammer trong phòng Lab (3 máy bắn 500Mbps).
        """
        channel_idx, bw_idx = action[0], action[1]

        real_p_c = float(current_real_state[1])
        real_retry = float(current_real_state[2])
        real_airtime = float(current_real_state[3])
        actual_sta = float(current_real_state[4])

        # Nếu p_c thực tế hoặc Airtime tăng vọt do UDP Jammer, quy đổi sang số lượng STA tương đương
        virtual_sta_from_stress = (real_p_c * 140.0) + (real_airtime * 80.0) + (real_retry * 60.0)
        effective_sta = max(actual_sta, virtual_sta_from_stress)

        noise_level = 0.1 if channel_idx == 0 else (0.2 if channel_idx == 1 else 0.4)
        base_throughput = 50.0 if bw_idx == 0 else (100.0 if bw_idx == 1 else 200.0)

        p_c = noise_level + (effective_sta * 0.005)

        if bw_idx == 2 and effective_sta > 100:
            p_c += 0.5
        elif bw_idx == 1 and effective_sta > 100:
            p_c += 0.2

        p_c = min(1.0, p_c)
        actual_throughput = base_throughput * (1.0 - p_c)

        norm_throughput = actual_throughput / 100.0
        penalty_collision = 5.0 * (p_c ** 2)
        expected_reward = norm_throughput - penalty_collision

        action_penalty = 0.0
        if self.previous_action is not None:
            if channel_idx != self.previous_action[0]:
                action_penalty -= 0.5
            if bw_idx != self.previous_action[1]:
                action_penalty -= 0.3

        return expected_reward + action_penalty