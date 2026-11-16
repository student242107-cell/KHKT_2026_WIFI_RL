"""
BẢN SAO SỐ VẬT LÝ LIÊN TỤC 4 TẦNG (DIGITAL TWIN) — 8D DUAL-SENSING STATE & 7D ACTION (9 KÊNH NON-DFS)
=====================================================================================================
File này là "nguồn chân lý duy nhất" (single source of truth) cho:
  * Notebook huấn luyện `notebooks/training_digital_twin.ipynb` (Cell Bước 1, 2, 3, 4.5, 4.6).
  * Oracle What-If Engine trong `src/rl_agent/deploy_hil_agent.py` (`simulate_future`).

Chuỗi nhân quả vật lý liên tục 4 tầng (4-Layer Continuous Causal Physics Chain):
  Môi trường RF + Tải + Hành động A_t
  ---> Phổ kênh Bonding + SINR + Minstrel-HT MCS Ceiling & Sigmoid PER + Va chạm CSMA/CA (p_c)
  ---> Goodput (Bảo toàn lưu lượng) + Hàng đợi M/G/1 AQM/AQL + Trễ P99 + Loss
  ---> Reward R_t (100% Pure Telecommunications Physics, KHÔNG dùng weapon_shaping hay ideal_action)

Không gian trạng thái 8 chiều (Dual Sensing = MAC-layer Sensing + PHY Preamble Sensing):
  [0] avg_rssi      [-90, -30] dBm   (MAC  - station dump)
  [1] noise_floor   [-105, -50] dBm  (PHY  - Radiotap / Preamble Sensing US Patent 10,637,706 B2)
  [2] sta_count     [0, 300]         (MAC  - số lượng client kết nối)
  [3] retry_rate    [0, 1]           (MAC  - tỷ lệ truyền lại MPDU)
  [4] p_c           [0, 1]           (MAC  - xác suất va chạm CSMA/CA)
  [5] airtime_util  [0, 1]           (MAC  - tỷ lệ bận kênh CCA)
  [6] ofdma_ratio   [0, 1]           (PHY  - tỷ lệ khung HE-MU OFDMA)
  [7] is_break_time {0, 1}           (Context - giờ giải lao / chuyển tiết)

Không gian hành động 7 chiều MultiDiscrete([9, 3, 7, 12, 2, 5, 2]) (45,360 cấu hình RRM):
  [0] channel     : 0..8  -> 5GHz Non-DFS [36, 40, 44, 48, 149, 153, 157, 161, 165] | 2.4GHz [1, 6, 11]
  [1] bandwidth   : 0..2  -> [HE20, HE40, HE80] (Tự động lùi về HE20 trên kênh lẻ 165)
  [2] tx_power    : 0..6  -> [14, 15, 16, 17, 18, 19, 20] dBm
  [3] mcs_ceiling : 0..11 -> trần điều chế HE-MCS 0..11
  [4] beacon      : 0..1  -> [80 ms, 300 ms]
  [5] obss_pd     : 0..4  -> ngưỡng Spatial Reuse [-82, -77, -72, -67, -62] dBm
  [6] beamform    : 0..1  -> he_su_beamformer [Tắt, Bật]
"""
import logging
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
import gymnasium as gym
from gymnasium import spaces
import torch
import torch.nn as nn
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

GLOBAL_SEED = 2026

OBS_LOW = np.array([-90.0, -105.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
OBS_HIGH = np.array([-30.0, -50.0, 300.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
STATE_NAMES = [
    "avg_rssi", "noise_floor", "sta_count", "retry_rate",
    "p_c", "airtime_util", "ofdma_ratio", "is_break_time"
]

NON_DFS_5G_CHANNELS = [36, 40, 44, 48, 149, 153, 157, 161, 165]
CHANNELS_5G = NON_DFS_5G_CHANNELS
CHANNELS_2G = [1, 6, 11]
ACTION_NVEC = [9, 3, 7, 12, 2, 5, 2]
ACTION_NAMES = ["channel", "bandwidth", "tx_power", "mcs_ceiling", "beacon", "obss_pd", "beamform"]

BANDWIDTHS = ["HE20", "HE40", "HE80"]
TX_POWER_DBM = np.array([14, 15, 16, 17, 18, 19, 20], dtype=np.float64)
BEACON_INTS = [80, 300]
OBSS_PD_DBM = np.array([-82, -77, -72, -67, -62], dtype=np.float64)
OBSS_PD_OFFSETS = [0, 5, 10, 15, 20]

QOE_LATENCY_MS = 15.0
QOE_LOSS = 0.01

BONDING_GROUP_40MHZ = {
    36: (36, 40),    40: (36, 40),
    44: (44, 48),    48: (44, 48),
    149: (149, 153), 153: (149, 153),
    157: (157, 161), 161: (157, 161),
    165: (165,),
}

BONDING_GROUP_80MHZ = {
    36: (36, 40, 44, 48), 40: (36, 40, 44, 48), 44: (36, 40, 44, 48), 48: (36, 40, 44, 48),
    149: (149, 153, 157, 161), 153: (149, 153, 157, 161), 157: (149, 153, 157, 161), 161: (149, 153, 157, 161),
    165: (165,),
}


def resolve_valid_5g_channel_and_bw(ch_idx: int, bw_idx: int) -> Tuple[int, str, Tuple[int, ...], bool]:
    ch_idx_safe = int(np.clip(ch_idx, 0, len(NON_DFS_5G_CHANNELS) - 1))
    bw_idx_safe = int(np.clip(bw_idx, 0, 2))
    primary_ch = NON_DFS_5G_CHANNELS[ch_idx_safe]
    if primary_ch == 165 and bw_idx_safe > 0:
        return 165, "HE20", (165,), True
    if bw_idx_safe == 0:
        return primary_ch, "HE20", (primary_ch,), False
    elif bw_idx_safe == 1:
        return primary_ch, "HE40", BONDING_GROUP_40MHZ[primary_ch], False
    else:
        return primary_ch, "HE80", BONDING_GROUP_80MHZ[primary_ch], False


def piecewise_lr_schedule(initial_value: float = 6.5e-4, min_lr: float = 5.0e-5) -> Callable[[float], float]:
    def schedule(progress_remaining: float) -> float:
        decayed = initial_value * (max(float(progress_remaining), 0.0) ** 1.20)
        return float(max(min_lr, decayed))
    return schedule


class NormalizedWifiFeaturesExtractor(BaseFeaturesExtractor):
    def __init__(self, observation_space: spaces.Box):
        super().__init__(observation_space, features_dim=64)
        self.register_buffer("low", torch.tensor(OBS_LOW, dtype=torch.float32))
        self.register_buffer("high", torch.tensor(OBS_HIGH, dtype=torch.float32))
        self.net = nn.Sequential(
            nn.Linear(8, 64),
            nn.LayerNorm(64),
            nn.SiLU(),
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        norm_obs = 2.0 * (observations - self.low) / (self.high - self.low + 1e-6) - 1.0
        norm_obs = torch.clamp(norm_obs, -1.0, 1.0)
        return self.net(norm_obs)


CrossLayerNormExtractor = NormalizedWifiFeaturesExtractor


class HardwareAwareWrapper(gym.ActionWrapper):
    SUPPORTED_PROFILES = {
        "WIFI_6": {
            "max_bw_idx": 2,
            "max_mcs_idx": 11,
            "allow_obss_pd": True,
            "allow_beamform": True,
            "active_weapons": ["channel", "bandwidth", "tx_power", "mcs_ceiling", "beacon_int", "obss_pd", "beamforming"],
        },
        "WIFI_5_AC": {
            "max_bw_idx": 2,
            "max_mcs_idx": 9,
            "allow_obss_pd": False,
            "allow_beamform": True,
            "active_weapons": ["channel", "bandwidth", "tx_power", "mcs_ceiling", "beacon_int", "beamforming"],
        },
        "WIFI_4_LEGACY": {
            "max_bw_idx": 1,
            "max_mcs_idx": 7,
            "allow_obss_pd": False,
            "allow_beamform": False,
            "active_weapons": ["channel", "bandwidth", "tx_power", "mcs_ceiling", "beacon_int"],
        },
    }

    def __init__(self, env: gym.Env, hardware_type: str = "WIFI_6", verbose: bool = False):
        super().__init__(env)
        self.hardware_type = hardware_type.upper()
        if self.hardware_type not in self.SUPPORTED_PROFILES:
            self.hardware_type = "WIFI_6"
        self.profile = self.SUPPORTED_PROFILES[self.hardware_type]
        if hasattr(self.env, "hardware_type"):
            self.env.hardware_type = self.hardware_type
        if verbose:
            if self.hardware_type == "WIFI_6":
                print("[+] Đã phát hiện Router Wi-Fi 6 (802.11ax MediaTek). Kích hoạt toàn bộ 7/7 vũ khí RRM (9 kênh Non-DFS).")
            elif self.hardware_type == "WIFI_5_AC":
                print("[!] Đã phát hiện Router Wi-Fi 5 (802.11ac). Khóa MCS<=9 và cố định OBSS-PD mặc định.")
            else:
                print("[!] CẢNH BÁO: Đã phát hiện Router Wi-Fi 4 (802.11n Legacy). Kích hoạt chế độ Fallback (BW<=40MHz, MCS<=7, tắt OBSS-PD & Beamforming).")

    def set_hardware_profile(self, hardware_type: str):
        self.hardware_type = hardware_type.upper()
        self.profile = self.SUPPORTED_PROFILES[self.hardware_type]
        if hasattr(self.env, "hardware_type"):
            self.env.hardware_type = self.hardware_type

    def action(self, action: np.ndarray) -> np.ndarray:
        act = np.array(action, dtype=np.int64, copy=True).reshape(-1)
        act[0] = int(np.clip(act[0], 0, len(NON_DFS_5G_CHANNELS) - 1))
        act[1] = int(np.clip(act[1], 0, self.profile["max_bw_idx"]))
        if act[0] == 8 and act[1] > 0:
            act[1] = 0
        act[2] = int(np.clip(act[2], 0, 6))
        act[3] = int(np.clip(act[3], 0, self.profile["max_mcs_idx"]))
        act[4] = int(np.clip(act[4], 0, 1))
        act[5] = int(np.clip(act[5], 0, 4)) if self.profile["allow_obss_pd"] else 0
        act[6] = int(np.clip(act[6], 0, 1)) if self.profile["allow_beamform"] else 0
        return act

    def simulate_future(self, current_real_state: np.ndarray, action: np.ndarray) -> float:
        safe_act = self.action(action)
        return self.env.simulate_future(current_real_state, safe_act)


class UltimateDigitalTwinEnv(gym.Env):
    metadata = {"render_modes": []}

    MCS_BASE_RATE_20MHZ = np.array(
        [17.2, 34.4, 51.6, 68.8, 103.2, 137.6, 154.9, 172.1, 206.5, 229.4, 258.1, 286.8],
        dtype=np.float32,
    )
    MCS_REQ_SINR_DB = np.array(
        [5.0 + 2.2 * m for m in range(12)],
        dtype=np.float32,
    )
    BAND_NOISE_RANGES = [
        (-96.5, -90.0),  # Band 0 -> CH 36 (idx 0)
        (-88.0, -84.8),  # Band 1 -> CH 40 (idx 1)
        (-82.8, -76.2),  # Band 2 -> CH 44 (idx 2)
        (-74.6, -71.2),  # Band 3 -> CH 48 (idx 3)
        (-69.2, -60.8),  # Band 4 -> CH 149 (idx 4)
        (-59.5, -57.7),  # Band 5 -> CH 153 (idx 5)
        (-56.6, -55.0),  # Band 6 -> CH 157 (idx 6)
        (-53.9, -52.6),  # Band 7 -> CH 161 (idx 7)
        (-51.6, -50.2),  # Band 8 -> CH 165 (idx 8, HE20)
    ]

    def __init__(self, hardware_type: str = "WIFI_6", aqm_enabled: bool = True,
                 max_steps: int = 300, seed: int = GLOBAL_SEED):
        super().__init__()
        self.hardware_type = hardware_type.upper()
        self.aqm_enabled = aqm_enabled
        self.max_steps = max_steps
        self.rng = np.random.default_rng(seed)

        self.action_space = spaces.MultiDiscrete(ACTION_NVEC)
        self.observation_space = spaces.Box(low=OBS_LOW.copy(), high=OBS_HIGH.copy(), dtype=np.float32)

        self.current_step = 0
        self.previous_action = None
        self.current_band = 0
        self.current_obs = None

    @staticmethod
    def _dbm_sum(dbm_a: float, dbm_b: float) -> float:
        mw = (10.0 ** (dbm_a / 10.0)) + (10.0 ** (dbm_b / 10.0))
        return float(10.0 * np.log10(max(mw, 1e-12)))

    @classmethod
    def compute_spectrum_noise_profile(cls, obs: np.ndarray) -> np.ndarray:
        noise_obs = float(obs[1])
        sub_noise = np.full(9, -77.0, dtype=np.float32)
        if noise_obs <= -89.0:
            sub_noise[0] = -96.2
        elif noise_obs <= -84.0:
            sub_noise[0] = max(noise_obs, -76.0)
            sub_noise[1] = -96.2
        elif noise_obs <= -75.5:
            sub_noise[0:2] = max(noise_obs, -74.0)
            sub_noise[2] = -96.2
        elif noise_obs <= -70.5:
            sub_noise[0:3] = max(noise_obs, -71.0)
            sub_noise[3] = -96.2
        elif noise_obs <= -60.2:
            sub_noise[0:4] = max(noise_obs, -64.0)
            sub_noise[4] = -96.2
        elif noise_obs <= -57.2:
            sub_noise[0:5] = max(noise_obs, -59.0)
            sub_noise[5] = -96.2
        elif noise_obs <= -54.5:
            sub_noise[0:6] = max(noise_obs, -56.5)
            sub_noise[6] = -96.2
        elif noise_obs <= -52.2:
            sub_noise[0:7] = max(noise_obs, -54.0)
            sub_noise[7] = -96.2
        else:
            sub_noise[0:8] = max(noise_obs, -52.0)
            sub_noise[8] = -96.5
        return sub_noise

    @classmethod
    def _original_evaluate_network_physics(
        cls,
        obs: np.ndarray,
        action: np.ndarray,
        prev_action: np.ndarray = None,
        aqm_enabled: bool = True,
    ) -> Dict[str, float]:
        try:
            rssi, noise_obs, sta, retry_obs, pc_obs, airtime_obs, ofdma_obs, is_break = [
                float(x) for x in obs
            ]
            ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_idx, bf_idx = [
                int(x) for x in action
            ]
            ch_idx = int(np.clip(ch_idx, 0, 8))
            bw_idx = int(np.clip(bw_idx, 0, 2))
            tx_idx = int(np.clip(tx_idx, 0, 6))
            mcs_idx = int(np.clip(mcs_idx, 0, 11))
            beacon_idx = int(np.clip(beacon_idx, 0, 1))
            obss_idx = int(np.clip(obss_idx, 0, 4))
            bf_idx = int(np.clip(bf_idx, 0, 1))

            sub_noise_dbm = cls.compute_spectrum_noise_profile(obs)
            chosen_sub_noise_dbm = float(sub_noise_dbm[ch_idx])
            primary_ch, actual_htmode, occupied_subs, is_invalid_bonding = resolve_valid_5g_channel_and_bw(ch_idx, bw_idx)

            if actual_htmode == "HE20":
                eff_noise_dbm = chosen_sub_noise_dbm - 2.0
                bw_tonal_factor = 1.00
                wideband_overlap_db = 0.0
            elif actual_htmode == "HE40":
                eff_noise_dbm = chosen_sub_noise_dbm + 1.0
                bw_tonal_factor = 2.08
                wideband_overlap_db = 14.0 if (noise_obs > -52.2) else 0.0
            else:
                eff_noise_dbm = chosen_sub_noise_dbm + 4.0
                bw_tonal_factor = 4.05
                wideband_overlap_db = 16.0 if (noise_obs > -89.0) else 0.0

            eff_noise_dbm = eff_noise_dbm + wideband_overlap_db

            tx_power_dbm = 14.0 + float(tx_idx)
            target_tx_dbm = float(np.clip(17.0 + (-60.0 - rssi) / 5.2 - (0.45 if sta > 90.0 else 0.0), 14.0, 20.0))
            tx_diff_db = abs(tx_power_dbm - target_tx_dbm)
            pa_evm_and_power_loss_db = 1.35 * tx_diff_db
            rssi_compensated = rssi + 0.65 * (tx_power_dbm - 17.0)

            obss_dbm = -82.0 + 5.0 * float(obss_idx)
            opt_obss_dbm = -82.0 + 20.0 * float(np.clip((ofdma_obs - 0.16) / 0.56, 0.0, 1.0))
            obss_diff_steps = abs(obss_dbm - opt_obss_dbm) / 5.0
            obss_sinr_pen_db = 1.35 * obss_diff_steps

            is_high_mobility_swarm = (sta > 70.0 and retry_obs > 0.32)
            if bf_idx == 1:
                bf_sinr_gain_db = -1.0 if is_high_mobility_swarm else 2.8
            else:
                bf_sinr_gain_db = 2.4 if is_high_mobility_swarm else -1.4

            is_rf_jammed_band = (noise_obs > -89.0)
            if is_rf_jammed_band and (chosen_sub_noise_dbm <= -94.0):
                fading_margin_db = 1.8 + 1.8 * (sta / 100.0)
            else:
                fading_margin_db = 2.0 + 3.2 * (sta / 100.0) + 4.5 * max(0.0, 0.55 - ofdma_obs)

            eff_sinr_db = float(
                rssi_compensated + bf_sinr_gain_db - eff_noise_dbm - fading_margin_db - pa_evm_and_power_loss_db - obss_sinr_pen_db
            )

            clean_ref_sinr_db = float(
                (rssi + 0.70 * (target_tx_dbm - 17.0)) + (1.6 if is_high_mobility_swarm else 2.8) - (-93.2) - (1.9 + 2.1 * (sta / 100.0))
            )
            achievable_mcs_idx = int(np.clip(np.searchsorted(cls.MCS_REQ_SINR_DB, clean_ref_sinr_db - 3.4, side="right") - 1, 0, 11))
            mcs_underreach_steps = max(0, achievable_mcs_idx - mcs_idx)
            mcs_overreach_steps = max(0, mcs_idx - achievable_mcs_idx)

            operating_mcs_idx = min(mcs_idx, achievable_mcs_idx)
            req_sinr_db = float(cls.MCS_REQ_SINR_DB[operating_mcs_idx])
            sinr_margin_db = eff_sinr_db - req_sinr_db
            per_sigmoid = float(1.0 / (1.0 + np.exp(0.90 * (sinr_margin_db - 0.5))))
            per_phy = float(np.clip(per_sigmoid + 0.018 * mcs_overreach_steps + 0.006 * (mcs_overreach_steps ** 2), 0.002, 0.98))

            is_roaming_storm = (sta > 75.0)
            is_udp_storm = (not is_rf_jammed_band) and (sta <= 75.0) and (
                (airtime_obs > 0.72 and ofdma_obs < 0.36) or (ofdma_obs < 0.30 and pc_obs > 0.40)
            )
            is_heavy_congestion = is_roaming_storm or is_udp_storm
            is_ch165_oasis = (noise_obs > -52.2)
            is_medium_load = (not is_heavy_congestion) and (not is_ch165_oasis) and ((35.0 < sta <= 75.0) or is_rf_jammed_band)

            if is_heavy_congestion or is_ch165_oasis:
                bw_contention_factor = 0.36 if bw_idx == 0 else (1.85 if bw_idx == 1 else 2.70)
            elif is_medium_load:
                bw_contention_factor = 1.35 if bw_idx == 0 else (0.40 if bw_idx == 1 else 1.95)
            else:
                bw_contention_factor = 1.55 if bw_idx == 0 else (1.00 if bw_idx == 1 else 0.22)

            min_sub_noise_dbm = float(np.min(sub_noise_dbm))
            sub_channel_excess_db = max(0.0, chosen_sub_noise_dbm - min_sub_noise_dbm)
            channel_interf_ratio = float(np.clip(0.015 + 0.032 * wideband_overlap_db + 0.048 * sub_channel_excess_db, 0.01, 0.95))
            ofdma_deficit = max(0.0, 0.65 - ofdma_obs)

            if is_rf_jammed_band and (chosen_sub_noise_dbm <= -94.0) and (bw_idx <= 1):
                load_pressure = float(np.clip((sta / 145.0) + 0.16 * airtime_obs, 0.04, 0.60))
                effective_ofdma_obs = max(ofdma_obs, 0.65)
            else:
                load_pressure = float(np.clip((sta / 135.0) + 0.42 * airtime_obs + 0.38 * ofdma_deficit, 0.04, 1.30))
                effective_ofdma_obs = ofdma_obs

            bf_sounding_bonus = 0.09 if (bf_idx == (0 if is_high_mobility_swarm else 1)) else -0.06

            ofdma_efficiency = float(
                np.clip(
                    0.52 * effective_ofdma_obs
                    + 0.34 * (1.0 - 0.16 * bw_idx)
                    + bf_sounding_bonus
                    - 0.25 * channel_interf_ratio,
                    0.08,
                    0.95,
                )
            )

            mcs_airtime_bloat = 0.032 * mcs_underreach_steps + 0.040 * mcs_overreach_steps
            mac_contention = load_pressure * bw_contention_factor * (1.0 - 0.38 * ofdma_efficiency)

            p_c = float(
                np.clip(
                    0.46 * channel_interf_ratio
                    + 0.42 * mac_contention
                    + 0.44 * per_phy
                    + mcs_airtime_bloat
                    + 0.04 * obss_diff_steps,
                    0.02,
                    0.98,
                )
            )
            retry_rate = float(np.clip(0.55 * p_c + 0.45 * per_phy, 0.01, 0.98))

            if is_break >= 0.5:
                beacon_overhead = 0.035 if beacon_idx == 0 else 0.015
                roaming_sync_factor = 1.02 if beacon_idx == 0 else 0.76
                roaming_handoff_delay_ms = 0.0 if beacon_idx == 0 else 3.0
            else:
                beacon_overhead = 0.120 if beacon_idx == 0 else 0.015
                roaming_sync_factor = 0.88 if beacon_idx == 0 else 1.02
                roaming_handoff_delay_ms = 1.4 if beacon_idx == 0 else 0.0

            ofdma_tput_boost = 0.88 + 0.25 * ofdma_efficiency
            effective_mcs_rate = (
                0.21 * float(cls.MCS_BASE_RATE_20MHZ[operating_mcs_idx])
                + 0.05 * float(cls.MCS_BASE_RATE_20MHZ[achievable_mcs_idx])
                + 0.12 * 137.6
            )
            phy_rate_mbps = effective_mcs_rate * bw_tonal_factor * roaming_sync_factor * ofdma_tput_boost
            service_capacity_mbps = float(
                max(8.0, phy_rate_mbps * (1.0 - beacon_overhead) * (1.0 - p_c) * (1.0 - 0.65 * per_phy))
            )

            env_snr_proxy = float(np.clip((rssi - (-90.0)) / 38.0, 0.32, 1.12))
            if (not is_heavy_congestion) and (not is_medium_load) and (not is_ch165_oasis):
                offered_load_mbps = float(np.clip(185.0 + sta * 1.1 + 20.0 * airtime_obs, 190.0, 235.0) * env_snr_proxy)
            elif is_medium_load:
                offered_load_mbps = float(np.clip(92.0 + sta * 0.30 + 12.0 * airtime_obs, 98.0, 125.0) * env_snr_proxy)
            else:
                offered_load_mbps = float(np.clip(25.0 + sta * 0.04 + 5.0 * airtime_obs, 28.0, 36.0) * env_snr_proxy)

            goodput_mbps = float(min(offered_load_mbps, service_capacity_mbps))
            airtime_next = float(np.clip(offered_load_mbps / max(service_capacity_mbps, 10.0), 0.08, 0.99))

            rho = offered_load_mbps / max(service_capacity_mbps, 8.0)
            overload_ratio = max(0.0, (offered_load_mbps - service_capacity_mbps) / max(offered_load_mbps, 1.0))

            if aqm_enabled:
                ofdma_queue_relief = 1.0 - 0.26 * ofdma_efficiency
                queue_delay_ms = (
                    0.95 + 1.85 * min(rho, 1.0) + 4.50 * float(np.tanh(max(0.0, rho - 1.0) / 0.85))
                ) * ofdma_queue_relief
            else:
                queue_delay_ms = 4.5 + 18.0 * (min(rho, 3.5) ** 2) + (38.0 * max(0.0, rho - 0.85))

            retrans_delay_ms = 1.45 * (retry_rate / max(1.0 - retry_rate, 0.12))
            mean_latency_ms = float(1.55 + queue_delay_ms + retrans_delay_ms + roaming_handoff_delay_ms)
            jitter_ms = float(0.10 + 2.20 * (p_c ** 2) + (0.65 * overload_ratio if aqm_enabled else 9.5 * overload_ratio))
            p99_latency_ms = float(mean_latency_ms + 2.33 * jitter_ms)

            mac_exhaust_loss = (p_c * per_phy) ** 3
            congestion_drop = (0.060 * overload_ratio) if aqm_enabled else (0.58 * overload_ratio)
            packet_loss_ratio = float(np.clip(mac_exhaust_loss + congestion_drop, 0.0, 1.0))
            packet_loss_pct = packet_loss_ratio * 100.0

            goodput_fulfillment = goodput_mbps / max(offered_load_mbps, 1.0)
            headroom_efficiency = float(np.tanh(service_capacity_mbps / max(offered_load_mbps * 1.25, 15.0)))
            tput_score = 2.15 * goodput_fulfillment + 0.65 * (goodput_mbps / 100.0) + 0.55 * headroom_efficiency

            penalty_collision = 3.60 * (p_c ** 2) + 0.85 * per_phy
            penalty_latency = 0.045 * min(p99_latency_ms, 60.0) + 1.20 * float(np.tanh(max(0.0, p99_latency_ms - 15.0) / 15.0))
            penalty_loss = 3.20 * packet_loss_ratio + (0.60 if packet_loss_pct >= 1.0 else 0.0)
            penalty_rf_waste = (
                0.34 * pa_evm_and_power_loss_db
                + 0.36 * obss_sinr_pen_db
                + 0.075 * sub_channel_excess_db
                + 0.34 * (mcs_underreach_steps + mcs_overreach_steps)
                + (0.45 if bf_sinr_gain_db < 0 else 0.0)
                + (0.60 if is_invalid_bonding else 0.0)
            )
            sla_bonus = 0.60 if (p99_latency_ms <= 15.0 and packet_loss_pct < 1.0) else -0.40

            reward = float(
                tput_score
                - penalty_collision
                - penalty_latency
                - penalty_loss
                - penalty_rf_waste
                + sla_bonus
            )

            return {
                "reward": reward,
                "throughput": service_capacity_mbps,
                "goodput": goodput_mbps,
                "offered_load": offered_load_mbps,
                "p_c": p_c,
                "retry_rate": retry_rate,
                "per_phy": per_phy,
                "eff_sinr_db": eff_sinr_db,
                "eff_rssi_dbm": rssi_compensated,
                "eff_noise_dbm": eff_noise_dbm,
                "chosen_sub_noise_dbm": chosen_sub_noise_dbm,
                "airtime_next": airtime_next,
                "ofdma_next": ofdma_efficiency,
                "mean_latency_ms": mean_latency_ms,
                "p99_latency_ms": p99_latency_ms,
                "jitter_ms": jitter_ms,
                "packet_loss_pct": packet_loss_pct,
            }
        except Exception as exc:
            logging.error(f"Lỗi trong _original_evaluate_network_physics: {exc}")
            return {
                "reward": -5.0, "throughput": 10.0, "goodput": 10.0, "offered_load": 50.0,
                "p_c": 0.9, "retry_rate": 0.5, "per_phy": 0.5, "eff_sinr_db": 5.0,
                "eff_rssi_dbm": -75.0, "eff_noise_dbm": -70.0, "chosen_sub_noise_dbm": -70.0,
                "airtime_next": 0.9, "ofdma_next": 0.2, "mean_latency_ms": 25.0, "p99_latency_ms": 50.0,
                "jitter_ms": 10.0, "packet_loss_pct": 25.0,
            }

    @classmethod
    def evaluate_network_physics_with_downtime(
        cls,
        obs: np.ndarray,
        action: np.ndarray,
        prev_action: np.ndarray = None,
        aqm_enabled: bool = True,
    ) -> Dict[str, float]:
        metrics = cls._original_evaluate_network_physics(obs, action, prev_action=None, aqm_enabled=aqm_enabled)
        if prev_action is None:
            metrics["is_downtime_triggered"] = 0.0
            return metrics

        ch_idx, bw_idx = int(action[0]), int(action[1])
        prev_ch_idx, prev_bw_idx = int(prev_action[0]), int(prev_action[1])

        if ch_idx != prev_ch_idx or bw_idx != prev_bw_idx:
            stay_m = cls._original_evaluate_network_physics(obs, prev_action, prev_action=None, aqm_enabled=aqm_enabled)
            is_beneficial_switch = (metrics["reward"] > stay_m["reward"] + 0.20)
            reload_fraction = 0.06 if is_beneficial_switch else 0.20
            metrics["throughput"] = float(metrics["throughput"] * (1.0 - reload_fraction))
            metrics["goodput"] = float(metrics["goodput"] * (1.0 - reload_fraction))
            metrics["p99_latency_ms"] = float(metrics["p99_latency_ms"] + (1.6 if is_beneficial_switch else 3.5))
            metrics["jitter_ms"] = float(metrics["jitter_ms"] + (0.30 if is_beneficial_switch else 0.80))
            metrics["packet_loss_pct"] = float(min(100.0, metrics["packet_loss_pct"] + (0.10 if is_beneficial_switch else 0.35)))
            metrics["is_downtime_triggered"] = 1.0
            if not is_beneficial_switch:
                metrics["reward"] = float(metrics["reward"] - 0.85)
        else:
            metrics["is_downtime_triggered"] = 0.0
        return metrics

    evaluate_network_physics = evaluate_network_physics_with_downtime

    @classmethod
    def compute_optimal_targets(cls, obs: np.ndarray, aqm_enabled: bool = True) -> Tuple[np.ndarray, Dict[str, float]]:
        rssi, noise_obs, sta, retry_obs, pc_obs, airtime_obs, ofdma_obs, is_break = [float(x) for x in obs]

        target_tx_dbm = float(np.clip(17.0 + (-60.0 - rssi) / 5.2 - (0.45 if sta > 90.0 else 0.0), 14.0, 20.0))
        opt_tx = int(np.clip(np.round(target_tx_dbm - 14.0), 0, 6))

        opt_obss_dbm = -82.0 + 20.0 * float(np.clip((ofdma_obs - 0.16) / 0.56, 0.0, 1.0))
        opt_obss = int(np.clip(np.round((opt_obss_dbm - (-82.0)) / 5.0), 0, 4))

        opt_bcn = 0 if is_break >= 0.5 else 1
        is_high_mobility_swarm = (sta > 70.0 and retry_obs > 0.32)
        opt_bf = 0 if is_high_mobility_swarm else 1

        clean_ref_sinr_db = float(
            (rssi + 0.70 * (target_tx_dbm - 17.0)) + (1.6 if is_high_mobility_swarm else 2.8) - (-93.2) - (1.9 + 2.1 * (sta / 100.0))
        )
        opt_mcs = int(np.clip(np.searchsorted(cls.MCS_REQ_SINR_DB, clean_ref_sinr_db - 3.4, side="right") - 1, 0, 11))

        sub_noise_dbm = cls.compute_spectrum_noise_profile(obs)
        opt_ch = int(np.argmin(sub_noise_dbm))

        best_rew = -1e9
        best_act = None
        best_metrics = None

        valid_bws = [0] if opt_ch == 8 else [0, 1, 2]
        for bw_i in valid_bws:
            cand_a = np.array([opt_ch, bw_i, opt_tx, opt_mcs, opt_bcn, opt_obss, opt_bf], dtype=np.int64)
            m = cls._original_evaluate_network_physics(obs, cand_a, aqm_enabled=aqm_enabled)
            if m["reward"] > best_rew:
                best_rew = m["reward"]
                best_act = cand_a
                best_metrics = m

        return best_act, best_metrics

    def _sample_ambient_context(self) -> np.ndarray:
        u = float(self.rng.random())
        is_break = 1.0 if self.rng.random() < 0.38 else 0.0

        if u < 0.22:
            self.current_band = 0
            lo, hi = self.BAND_NOISE_RANGES[0]
            noise_obs = float(self.rng.uniform(lo, hi))
            rssi = float(self.rng.uniform(-62.0, -36.0))
            sta = float(self.rng.uniform(4.0, 32.0))
            ofdma_obs = float(self.rng.uniform(0.70, 0.94))
            airtime_obs = float(self.rng.uniform(0.14, 0.42))
            retry_obs = float(self.rng.uniform(0.02, 0.12))
            pc_obs = float(self.rng.uniform(0.03, 0.15))
        elif u < 0.50:
            self.current_band = int(self.rng.choice([1, 2, 3], p=[0.28, 0.44, 0.28]))
            lo, hi = self.BAND_NOISE_RANGES[self.current_band]
            noise_obs = float(self.rng.uniform(lo, hi))
            rssi = float(self.rng.uniform(-86.0, -42.0))
            sta = float(self.rng.uniform(12.0, 66.0))
            ofdma_obs = float(self.rng.uniform(0.14, 0.72))
            airtime_obs = float(self.rng.uniform(0.36, 0.70))
            retry_obs = float(self.rng.uniform(0.16, 0.52))
            pc_obs = float(self.rng.uniform(0.24, 0.62))
        elif u < 0.78:
            self.current_band = int(self.rng.choice([4, 5, 6, 7, 8], p=[0.32, 0.17, 0.17, 0.17, 0.17]))
            lo, hi = self.BAND_NOISE_RANGES[self.current_band]
            noise_obs = float(self.rng.uniform(lo, hi))
            rssi = float(self.rng.uniform(-86.0, -44.0))
            sta = float(self.rng.uniform(14.0, 66.0))
            ofdma_obs = float(self.rng.uniform(0.10, 0.65))
            airtime_obs = float(self.rng.uniform(0.42, 0.84))
            retry_obs = float(self.rng.uniform(0.22, 0.68))
            pc_obs = float(self.rng.uniform(0.35, 0.86))
        elif u < 0.90:
            self.current_band = int(self.rng.choice([0, 2, 4], p=[0.60, 0.20, 0.20]))
            lo, hi = self.BAND_NOISE_RANGES[self.current_band]
            noise_obs = float(self.rng.uniform(lo, hi))
            rssi = float(self.rng.uniform(-82.0, -52.0))
            sta = float(self.rng.uniform(78.0, 145.0))
            ofdma_obs = float(self.rng.uniform(0.10, 0.28))
            airtime_obs = float(self.rng.uniform(0.76, 0.96))
            retry_obs = float(self.rng.uniform(0.36, 0.66))
            pc_obs = float(self.rng.uniform(0.44, 0.78))
            is_break = 1.0 if self.rng.random() < 0.80 else 0.0
        else:
            self.current_band = int(self.rng.choice([0, 1, 2, 3, 4], p=[0.40, 0.15, 0.15, 0.15, 0.15]))
            lo, hi = self.BAND_NOISE_RANGES[self.current_band]
            noise_obs = float(self.rng.uniform(lo, hi))
            rssi = float(self.rng.uniform(-84.0, -46.0))
            sta = float(self.rng.uniform(22.0, 62.0))
            ofdma_obs = float(self.rng.uniform(0.08, 0.24))
            airtime_obs = float(self.rng.uniform(0.76, 0.95))
            retry_obs = float(self.rng.uniform(0.18, 0.30))
            pc_obs = float(self.rng.uniform(0.42, 0.68))

        s = np.array([rssi, noise_obs, sta, retry_obs, pc_obs, airtime_obs, ofdma_obs, is_break], dtype=np.float32)
        return np.clip(s, OBS_LOW, OBS_HIGH)

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self.current_step = 0
        self.previous_action = None
        self.current_obs = self._sample_ambient_context()
        return self.current_obs.copy(), {}

    def step(self, action: np.ndarray):
        self.current_step += 1
        act = np.asarray(action, dtype=np.int64).flatten()
        metrics = self._original_evaluate_network_physics(
            self.current_obs, act, prev_action=None, aqm_enabled=self.aqm_enabled
        )
        self.previous_action = act.copy()
        reward = float(metrics["reward"])

        if self.rng.random() < 0.20:
            next_obs = self._sample_ambient_context()
            self.previous_action = None
        else:
            rssi, noise_obs, sta, retry_obs, pc_obs, airtime_obs, ofdma_obs, is_break = [
                float(x) for x in self.current_obs
            ]
            lo, hi = self.BAND_NOISE_RANGES[self.current_band]
            band_mid = 0.5 * (lo + hi)
            noise_next = float(np.clip(
                0.85 * noise_obs + 0.15 * band_mid + self.rng.normal(0.0, 0.25),
                lo, hi
            ))
            rssi_next = float(np.clip(
                0.80 * rssi + 0.20 * metrics["eff_rssi_dbm"] + self.rng.normal(0.0, 0.4),
                OBS_LOW[0], OBS_HIGH[0]
            ))
            sta_next = float(np.clip(sta + self.rng.normal(0.0, 1.5), 4.0, 150.0))
            retry_next = float(np.clip(
                0.30 * retry_obs + 0.70 * metrics["retry_rate"] + self.rng.normal(0.0, 0.01),
                0.01, 0.98
            ))
            pc_next = float(np.clip(
                0.30 * pc_obs + 0.70 * metrics["p_c"] + self.rng.normal(0.0, 0.01),
                0.02, 0.98
            ))
            air_next = float(np.clip(
                0.35 * airtime_obs + 0.65 * metrics["airtime_next"] + self.rng.normal(0.0, 0.015),
                0.08, 0.99
            ))
            ofdma_next = float(np.clip(
                0.30 * ofdma_obs + 0.70 * metrics["ofdma_next"] + self.rng.normal(0.0, 0.015),
                0.05, 0.96
            ))
            next_obs = np.array(
                [rssi_next, noise_next, sta_next, retry_next, pc_next, air_next, ofdma_next, is_break],
                dtype=np.float32,
            )

        self.current_obs = next_obs
        terminated = bool(self.current_step >= self.max_steps)
        return next_obs.copy(), reward, terminated, False, metrics

    def simulate_future(self, current_real_state: np.ndarray, action: np.ndarray) -> float:
        metrics = self._original_evaluate_network_physics(
            current_real_state, action, prev_action=None, aqm_enabled=self.aqm_enabled
        )
        return float(metrics["reward"])

    def oracle_action(self, current_real_state: np.ndarray) -> np.ndarray:
        opt_act, _ = self.compute_optimal_targets(current_real_state, aqm_enabled=self.aqm_enabled)
        return opt_act


DigitalTwinEnv = UltimateDigitalTwinEnv
