import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import gymnasium as gym
import numpy as np
import yaml
from gymnasium import spaces

from src.envs.digital_twin_env import (
    BEACON_INTS,
    CHANNELS_2G,
    HardwareAwareWrapper,
    NON_DFS_5G_CHANNELS,
    OBSS_PD_OFFSETS,
    OBS_HIGH,
    OBS_LOW,
    TX_POWER_DBM,
    UltimateDigitalTwinEnv,
    resolve_valid_5g_channel_and_bw,
)
from src.modules import wifi_actuators as actuators

PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"
CONFIG_PATH = "configs/network_config.yaml"


class RealRouterEnv(gym.Env):
    """
    Môi trường Testbed Thực tế (Hardware-In-The-Loop).
    Đồng bộ 1-1 với `UltimateDigitalTwinEnv`:
      - State 8D Dual-Sensing [avg_rssi, noise_floor, sta_count, retry_rate, p_c, airtime_util, ofdma_ratio, is_break_time]
      - Action 7D MultiDiscrete([9, 3, 7, 12, 2, 5, 2]) thực thi đủ cả 7 vũ khí (kể cả MCS Ceiling & OBSS-PD)
      - Reward tính thống nhất theo thang đo Cross-Layer Physics + Switching/AMC Shaping
    Đã chuẩn hóa cho phần cứng Xiaomi AX3000T (OpenWrt MediaTek MT7981B - IEEE 802.11ax).
    """

    def __init__(
        self,
        target_ap_ip: str,
        target_radio: str = "radio1",
        target_iface: str = "phy1-ap0",
        router_user: str = "root",
        router_pass: Optional[str] = "pass",
        step_interval: float = 10.0,
        max_steps: int = 100,
    ):
        super().__init__()
        self.target_ap_ip = target_ap_ip
        self.target_radio = target_radio
        self.target_iface = target_iface
        self.router_user = router_user
        self.router_pass = router_pass
        self.step_interval = float(step_interval)
        self.max_steps = int(max_steps)

        self.channels = list(NON_DFS_5G_CHANNELS) if self.target_radio == "radio1" else list(CHANNELS_2G)
        self.bandwidths = ["HE20", "HE40", "HE80"] if self.target_radio == "radio1" else ["HE20", "HE40", "HE40"]

        self.target_bssid = self._get_bssid_from_ip(target_ap_ip)
        if not self.target_bssid:
            logging.warning(f"Chưa map được MAC cho IP {target_ap_ip}. Fine-tune có thể đọc sai State!")

        self.action_space = spaces.MultiDiscrete([9, 3, 7, 12, 2, 5, 2])
        self.observation_space = spaces.Box(
            low=OBS_LOW.copy(),
            high=OBS_HIGH.copy(),
            dtype=np.float32,
        )
        self.current_step = 0
        self.previous_action: Optional[np.ndarray] = None
        self.last_obs: Optional[np.ndarray] = None

    def _get_bssid_from_ip(self, ip: str) -> Optional[str]:
        try:
            with open(CONFIG_PATH, "r") as file:
                config = yaml.safe_load(file)
                topo = config.get("network_topology", {})
                key = "ap_map" if self.target_radio == "radio1" else "ap_map_2g"
                ap_map = topo.get(key, topo.get("ap_map", {}))
                for mac, mapped_ip in ap_map.items():
                    if mapped_ip == ip:
                        return mac.upper()
        except Exception:
            pass
        return None

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[Dict[str, Any]] = None
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        super().reset(seed=seed)
        self.current_step = 0
        self.previous_action = None
        obs = self._get_real_state()
        self.last_obs = obs.copy()
        return obs, {}

    def _get_real_state(self) -> np.ndarray:
        try:
            with open(PROCESSED_DATA_PATH, "r") as f:
                json_data = json.load(f)
            if self.target_bssid and self.target_bssid in json_data:
                data = json_data[self.target_bssid]
            else:
                first_mac = list(json_data.keys())[0]
                data = json_data[first_mac]

            noise_val = data.get("noise_floor", data.get("phy_sensing", {}).get("noise_floor_dbm", -95.0))
            noise_val = -95.0 if noise_val is None else float(noise_val)
            ofdma_val = data.get("ofdma_ratio", data.get("phy_sensing", {}).get("ofdma_ratio", 0.5))
            ofdma_val = 0.5 if ofdma_val is None else float(ofdma_val)

            state_array = np.array(
                [
                    float(data["avg_rssi"]),
                    noise_val,
                    float(data["sta_count"]),
                    float(data["retry_rate"]),
                    float(data["p_c"]),
                    float(data["airtime_util"]),
                    ofdma_val,
                    float(data.get("is_break_time", 0)),
                ],
                dtype=np.float32,
            )
            return np.clip(state_array, OBS_LOW, OBS_HIGH).astype(np.float32)
        except Exception:
            return np.clip(
                np.array([-70.0, -95.0, 20.0, 0.05, 0.10, 0.40, 0.50, 0.0], dtype=np.float32),
                OBS_LOW,
                OBS_HIGH,
            )

    def _decode_action(self, action: np.ndarray) -> Dict[str, Any]:
        act = np.asarray(action, dtype=np.int64).reshape(-1)
        ch_idx = int(np.clip(act[0], 0, len(self.channels) - 1))
        bw_idx = int(np.clip(act[1], 0, 2))
        if self.target_radio == "radio1":
            target_channel, target_bw, _, bw_capped = resolve_valid_5g_channel_and_bw(ch_idx, bw_idx)
            if bw_capped:
                bw_idx = 0
        else:
            target_channel = self.channels[ch_idx]
            target_bw = self.bandwidths[bw_idx]

        tx_idx = int(np.clip(act[2], 0, len(TX_POWER_DBM) - 1))
        mcs_idx = int(np.clip(act[3], 0, 11))
        bcn_idx = int(np.clip(act[4], 0, len(BEACON_INTS) - 1))
        obss_idx = int(np.clip(act[5], 0, len(OBSS_PD_OFFSETS) - 1))
        bf_idx = int(np.clip(act[6], 0, 1))

        exec_act = np.array([ch_idx, bw_idx, tx_idx, mcs_idx, bcn_idx, obss_idx, bf_idx], dtype=np.int64)
        return {
            "exec_action": exec_act,
            "channel": target_channel,
            "bandwidth": target_bw,
            "tx_power_dbm": int(TX_POWER_DBM[tx_idx]),
            "mcs_ceiling": mcs_idx,
            "beacon_int": int(BEACON_INTS[bcn_idx]),
            "obss_pd_offset": int(OBSS_PD_OFFSETS[obss_idx]),
            "enable_bf": bool(bf_idx == 1),
        }

    def step(self, action: np.ndarray):
        self.current_step += 1
        cfg = self._decode_action(action)
        exec_act = cfg["exec_action"]

        obs_ref = self.last_obs if self.last_obs is not None else self._get_real_state()
        adaptive_aql = actuators.compute_adaptive_aql(
            bandwidth=cfg["bandwidth"],
            p_c=float(obs_ref[4]),
            airtime_util=float(obs_ref[5]),
            sta_count=int(obs_ref[2]),
        )

        logging.info(
            f"Fine-Tuning: Đang thử nghiệm CH={cfg['channel']}, BW={cfg['bandwidth']}, "
            f"Tx={cfg['tx_power_dbm']}dBm, MCS<={cfg['mcs_ceiling']}, Bcn={cfg['beacon_int']}ms, "
            f"OBSS={-82 + cfg['obss_pd_offset']}dBm, BF={'ON' if cfg['enable_bf'] else 'OFF'} "
            f"trên {self.target_ap_ip} ({self.target_radio})"
        )

        # Thực thi đầy đủ cả 7 vũ khí RRM xuống phần cứng OpenWrt
        actuators.apply_ai_profile_atomic(
            ap_ip=self.target_ap_ip,
            username=self.router_user,
            password=self.router_pass,
            radio_band=self.target_radio,
            interface=self.target_iface,
            channel=cfg["channel"],
            bandwidth=cfg["bandwidth"],
            tx_power_dbm=cfg["tx_power_dbm"],
            beacon_int=cfg["beacon_int"],
            enable_bf=cfg["enable_bf"],
            aql_limit=adaptive_aql,
            enable_atf=True,
            obss_pd_offset=cfg["obss_pd_offset"],
        )
        actuators.apply_mcs_ceiling(
            self.target_ap_ip,
            self.router_user,
            self.router_pass,
            self.target_iface,
            cfg["mcs_ceiling"],
        )

        if self.step_interval > 0.0:
            time.sleep(self.step_interval)

        new_state = self._get_real_state()

        # Tính phần thưởng đồng bộ 1-1 với thang đo Vật lý Cross-Layer trên Telemetry thực đo
        measured_noise_vec = np.full(len(NON_DFS_5G_CHANNELS), -95.0, dtype=np.float32)
        if self.target_radio == "radio1":
            measured_noise_vec[int(exec_act[0])] = float(new_state[1])
        else:
            measured_noise_vec[0] = float(new_state[1])

        phys_metrics = UltimateDigitalTwinEnv.evaluate_network_physics(
            new_state,
            exec_act,
            prev_action=self.previous_action,
            aqm_enabled=True,
            sub_noise_dbm=measured_noise_vec,
        )

        self.previous_action = exec_act.copy()
        self.last_obs = new_state.copy()
        terminated = bool(self.current_step >= self.max_steps)
        info = {"exec_action": exec_act, "physics_metrics": phys_metrics, "config": cfg}
        return new_state, float(phys_metrics["reward"]), terminated, False, info


def make_real_router_env(hardware_type: str = "WIFI_6", **kwargs) -> gym.Env:
    """Factory chuẩn hóa bọc HardwareAwareWrapper tương thích VecFrameStack(4)."""
    return HardwareAwareWrapper(RealRouterEnv(**kwargs), hardware_type=hardware_type)
