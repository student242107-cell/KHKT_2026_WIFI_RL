# Nội dung chuẩn hóa cho src/envs/real_router_env.py
import gymnasium as gym
from gymnasium import spaces
from typing import Any
import numpy as np
import time
import json
import logging
import yaml
from src.modules import wifi_actuators as actuators

PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"
CONFIG_PATH = "configs/network_config.yaml"


class RealRouterEnv(gym.Env):
    """
    Môi trường Testbed Thực tế (Hardware-In-The-Loop).
    Dành cho việc Fine-tuning mô hình AI đã pre-train trên Digital Twin.
    Đã chuẩn hóa cho phần cứng Xiaomi AX3000T (OpenWrt MediaTek MT7981B - IEEE 802.11ax).
    """
    def __init__(self, target_ap_ip, target_radio="radio1", target_iface="phy1-ap0",
                 router_user="root", router_pass="pass"):
        super().__init__()
        self.target_ap_ip = target_ap_ip
        self.target_radio = target_radio
        self.target_iface = target_iface
        self.router_user = router_user
        self.router_pass = router_pass

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
        try:
            with open(CONFIG_PATH, 'r') as file:
                config = yaml.safe_load(file)
                topo = config.get('network_topology', {})
                key = 'ap_map' if self.target_radio == "radio1" else 'ap_map_2g'
                ap_map = topo.get(key, topo.get('ap_map', {}))
                for mac, mapped_ip in ap_map.items():
                    if mapped_ip == ip:
                        return mac.upper()
        except Exception:
            pass
        return None

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        self.current_step = 0
        self.previous_action = None
        return self._get_real_state(), {}

    def _get_real_state(self):
        try:
            with open(PROCESSED_DATA_PATH, 'r') as f:
                json_data = json.load(f)
                if self.target_bssid and self.target_bssid in json_data:
                    data = json_data[self.target_bssid]
                else:
                    first_mac = list(json_data.keys())[0]
                    data = json_data[first_mac]

            state_array = np.array([
                data["avg_rssi"], data["p_c"], data["retry_rate"],
                data["airtime_util"], data["sta_count"],
                data["hour"], data["minute"], data["is_break_time"]
            ], dtype=np.float32)
            return np.clip(state_array, [-90.0, 0.0, 0.0, 0.0, 0, 0, 0, 0], [-30.0, 1.0, 1.0, 1.0, 300, 23, 59, 1])
        except Exception:
            return np.array([-70.0, 0.1, 0.05, 0.4, 20, 8, 15, 0], dtype=np.float32)

    def step(self, action):
        self.current_step += 1

        CHANNELS = [36, 40, 44] if self.target_radio == "radio1" else [1, 6, 11]
        BANDWIDTHS = ["HE20", "HE40", "HE80"] if self.target_radio == "radio1" else ["HE20", "HE40", "HE40"]
        BEACON_INTS = [80, 300]

        target_channel = CHANNELS[action[0]]
        target_bw = BANDWIDTHS[action[1]]
        target_tx = 8 + action[2]
        target_beacon = BEACON_INTS[action[4]]
        target_beamform = True if action[6] == 1 else False

        logging.info(
            f"Fine-Tuning: Đang thử nghiệm CH={target_channel}, BW={target_bw}, "
            f"Tx={target_tx}dBm trên {self.target_ap_ip} ({self.target_radio})"
        )

        # Sử dụng hàm nguyên tử để tránh gọi wifi reload nhiều lần
        actuators.apply_ai_profile_atomic(
            ap_ip=self.target_ap_ip,
            username=self.router_user,
            password=self.router_pass,
            radio_band=self.target_radio,
            interface=self.target_iface,
            channel=target_channel,
            bandwidth=target_bw,
            tx_power_dbm=target_tx,
            beacon_int=target_beacon,
            enable_bf=target_beamform
        )

        time.sleep(10)

        new_state = self._get_real_state()
        real_pc = new_state[1]
        actual_throughput = 100.0 * (1.0 - real_pc)

        norm_throughput = actual_throughput / 100.0
        penalty_collision = 5.0 * (real_pc ** 2)
        base_reward = norm_throughput - penalty_collision

        action_penalty = 0.0
        if self.previous_action is not None:
            if action[0] != self.previous_action[0]:
                action_penalty -= 0.5
            if action[1] != self.previous_action[1]:
                action_penalty -= 0.3

        self.previous_action = action.copy()
        final_reward = base_reward + action_penalty
        terminated = bool(self.current_step >= self.max_steps)

        return new_state, float(final_reward), terminated, False, {}