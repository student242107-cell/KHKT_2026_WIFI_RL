import gymnasium as gym
from gymnasium import spaces
from typing import Any
import numpy as np
import time
import json
import logging
import yaml # Thêm yaml để đọc mapping
from src.modules import wifi_actuators as actuators

PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"
CONFIG_PATH = "configs/network_config.yaml"

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
        
        # --- BẢN VÁ 1: TÌM BSSID CỦA IP MỤC TIÊU ---
        self.target_bssid = self._get_bssid_from_ip(target_ap_ip)
        if not self.target_bssid:
            logging.warning(f"Chưa map được MAC cho IP {target_ap_ip}. Fine-tune có thể đọc sai State!")
            
        self.action_space = spaces.MultiDiscrete([3, 3, 7, 12, 2, 5, 2])
        self.observation_space = spaces.Box(
            low=np.array([-90.0, 0.0, 0.0, 0.0, 0, 0, 0, 0], dtype=np.float32), 
            high=np.array([-30.0, 1.0, 1.0, 1.0, 300, 23, 59, 1], dtype=np.float32)
        )
        self.current_step = 0
        self.max_steps = 100 
        self.previous_action = None

    def _get_bssid_from_ip(self, ip):
        """Đọc file YAML để tìm MAC (BSSID) tương ứng với IP"""
        try:
            with open(CONFIG_PATH, 'r') as file:
                config = yaml.safe_load(file)
                ap_map = config.get('network_topology', {}).get('ap_map', {})
                # Tìm key (MAC) có value (IP) khớp với target_ap_ip
                for mac, mapped_ip in ap_map.items():
                    if mapped_ip == ip:
                        return mac.upper()
        except Exception:
            pass
        return None

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None,) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        self.current_step = 0
        self.previous_action = None
        return self._get_real_state(), {}

    def _get_real_state(self):
        """Đọc State THẬT của ĐÚNG con AP mục tiêu"""
        try:
            with open(PROCESSED_DATA_PATH, 'r') as f:
                json_data = json.load(f)
                
                # --- BẢN VÁ 1 (Tiếp): Đọc bệnh án đúng người ---
                if self.target_bssid and self.target_bssid in json_data:
                    data = json_data[self.target_bssid]
                else:
                    # Rơi vào trường hợp dự phòng nếu chưa map được
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
        
        # --- BẢN VÁ 2: GỬI TOÀN BỘ CÁC LỆNH ĐIỀU KHIỂN ---
        CHANNELS = [36, 40, 44] if self.target_radio == "radio0" else [1, 6, 11]
        BANDWIDTHS = ["HT20", "VHT40", "VHT80"]
        BEACON_INTS = [80, 300]
        
        target_channel = CHANNELS[action[0]]
        target_bw = BANDWIDTHS[action[1]]
        target_tx = 8 + action[2]
        target_mcs = action[3]
        target_beacon = BEACON_INTS[action[4]]
        
        logging.info(f"Fine-Tuning: Đang thử nghiệm cấu hình CH={target_channel}, BW={target_bw}, Tx={target_tx} trên {self.target_ap_ip}")
        
        # Bắn lệnh xuống Router
        # (Ở OpenWrt, để đổi kênh độc lập bạn có thể cần 1 hàm riêng trong actuators, hoặc ghép vào lệnh BW)
        actuators.adaptive_bandwidth_fallback(self.target_ap_ip, self.router_user, self.router_pass, self.target_radio, target_bw)
        actuators.optimize_tx_power(self.target_ap_ip, self.router_user, self.router_pass, self.target_iface, target_tx)
        actuators.apply_mcs_ceiling([self.target_ap_ip], self.router_user, self.router_pass, self.target_iface, mcs_limit=target_mcs)
        actuators.adjust_beacon_interval([self.target_ap_ip], self.router_user, self.router_pass, self.target_radio, interval_ms=target_beacon)
        
        # 2. ĐỢI MẠNG ỔN ĐỊNH VÀ SCAPY GOM ĐỦ DỮ LIỆU
        time.sleep(30)
        
        # 3. ĐỌC KẾT QUẢ THỰC TẾ
        new_state = self._get_real_state()
        real_pc = new_state[1]
        
        # Ước tính Throughput
        actual_throughput = 100.0 * (1.0 - real_pc) 
        
        # CHUẨN HÓA REWARD ĐỒNG BỘ VỚI DIGITAL TWIN
        norm_throughput = actual_throughput / 100.0
        penalty_collision = 5.0 * (real_pc ** 2)
        
        base_reward = norm_throughput - penalty_collision
        
        action_penalty = 0.0
        if self.previous_action is not None:
            if action[0] != self.previous_action[0]: action_penalty -= 0.5
            if action[1] != self.previous_action[1]: action_penalty -= 0.3
                
        self.previous_action = action.copy()
        final_reward = base_reward + action_penalty
        terminated = bool(self.current_step >= self.max_steps)
        
        return new_state, float(final_reward), terminated, False, {}