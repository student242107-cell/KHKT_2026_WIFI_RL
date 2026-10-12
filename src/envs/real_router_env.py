import gymnasium as gym
from gymnasium import spaces
from typing import Any
import numpy as np
import time
import json
import logging
from src.modules import wifi_actuators as actuators

PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"

class RealRouterEnv(gym.Env):
    """
    Môi trường Testbed Thực tế (Hardware-In-The-Loop).
    Dành cho việc Fine-tuning mô hình AI đã pre-train trên Digital Twin.
    """
    def __init__(self, target_ap_ip, target_radio="radio0", target_iface="wlan0", router_user="root", router_pass="pass"):
        super().__init__()
        self.target_ap_ip = target_ap_ip
        self.target_radio = target_radio
        self.target_iface = target_iface
        self.router_user = router_user
        self.router_pass = router_pass
        
        self.action_space = spaces.MultiDiscrete([3, 3, 7, 12, 2, 5, 2])
        self.observation_space = spaces.Box(
            low=np.array([-90.0, 0.0, 0.0, 0.0, 0, 0, 0, 0], dtype=np.float32), 
            high=np.array([-30.0, 1.0, 1.0, 1.0, 300, 23, 59, 1], dtype=np.float32)
        )
        self.current_step = 0
        self.max_steps = 100 # Chạy thực tế chỉ cần fine-tune số vòng nhỏ
        self.previous_action = None

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None,) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        self.current_step = 0
        self.previous_action = None
        return self._get_real_state(), {}

    def _get_real_state(self):
        """Đọc State THẬT của con AP mục tiêu từ file JSON của Scapy"""
        try:
            with open(PROCESSED_DATA_PATH, 'r') as f:
                json_data = json.load(f)
                # Giả định ta tìm State dựa vào IP (Cần logic mapping từ IP ngược ra MAC nếu file JSON dùng MAC làm Key)
                # Ở đây viết rút gọn lấy state đầu tiên thấy được
                first_mac = list(json_data.keys())[0]
                data = json_data[first_mac]
                
            state_array = np.array([
                data["avg_rssi"], data["p_c"], data["retry_rate"],
                data["airtime_util"], data["sta_count"],
                data["hour"], data["minute"], data["is_break_time"]
            ], dtype=np.float32)
            return np.clip(state_array, [-90.0, 0.0, 0.0, 0.0, 0, 0, 0, 0], [-30.0, 1.0, 1.0, 1.0, 300, 23, 59, 1])
        except:
            return np.array([-70.0, 0.1, 0.05, 0.4, 20, 8, 15, 0], dtype=np.float32)

    def step(self, action):
        self.current_step += 1
        
        # 1. GỬI LỆNH THỰC TẾ XUỐNG ROUTER BẰNG ACTUATORS
        CHANNELS = [36, 40, 44]
        BANDWIDTHS = ["HT20", "VHT40", "VHT80"]
        target_bw = BANDWIDTHS[action[1]]
        target_tx = 8 + action[2]
        
        logging.info(f"Fine-Tuning: Đang thử nghiệm cấu hình BW={target_bw}, Tx={target_tx} trên {self.target_ap_ip}")
        actuators.adaptive_bandwidth_fallback(self.target_ap_ip, self.router_user, self.router_pass, self.target_radio, target_bw)
        actuators.optimize_tx_power(self.target_ap_ip, self.router_user, self.router_pass, self.target_iface, target_tx)
        
        # 2. ĐỢI MẠNG ỔN ĐỊNH VÀ SCAPY GOM ĐỦ DỮ LIỆU
        time.sleep(30)
        
        # 3. ĐỌC KẾT QUẢ THỰC TẾ
        new_state = self._get_real_state()
        real_pc = new_state[1]
        real_throughput = 100 * (1 - real_pc) # Ước tính
        
        # Tính Reward thực tế
        base_reward = real_throughput - (500.0 * (real_pc ** 2))
        
        action_penalty = 0.0
        if self.previous_action is not None:
            if action[0] != self.previous_action[0]: action_penalty -= 5.0
            if action[1] != self.previous_action[1]: action_penalty -= 3.0
                
        self.previous_action = action.copy()
        terminated = bool(self.current_step >= self.max_steps)
        
        return new_state, float(base_reward + action_penalty), terminated, False, {}