"""
BẢN SAO SỐ CROSS-LAYER (DIGITAL TWIN) — 8D DUAL-SENSING STATE & 7D ACTION
=========================================================================
File này là "nguồn chân lý duy nhất" (single source of truth) cho:
  * Notebook huấn luyện `notebooks/training_digital_twin.ipynb` (Cell Bước 1, 2, 3, 4.5, 4.6).
  * Oracle What-If Engine trong `src/rl_agent/deploy_hil_agent.py` (`simulate_future`).

Chuỗi nhân quả vật lý 4 tầng (4-Layer Causal Physics Chain):
  Môi trường RF + Tải + Hành động A_t
  ---> Phổ kênh Bonding + SINR + PHY Rate + Va chạm CSMA/CA (p_c)
  ---> Goodput (Bảo toàn lưu lượng) + Hàng đợi AQM/AQL + Trễ P99 + Loss
  ---> Reward R_t

Không gian trạng thái 8 chiều (Dual Sensing = MAC-layer Sensing + PHY Preamble Sensing):
  [0] avg_rssi      [-90, -30] dBm   (MAC  - station dump)
  [1] noise_floor   [-105, -50] dBm  (PHY  - Radiotap / Preamble Sensing US Patent 10,637,706 B2)
  [2] sta_count     [0, 300]         (MAC  - số lượng client kết nối)
  [3] retry_rate    [0, 1]           (MAC  - tỷ lệ truyền lại MPDU)
  [4] p_c           [0, 1]           (MAC  - xác suất va chạm CSMA/CA)
  [5] airtime_util  [0, 1]           (MAC  - tỷ lệ bận kênh CCA)
  [6] ofdma_ratio   [0, 1]           (PHY  - tỷ lệ khung HE-MU OFDMA)
  [7] is_break_time {0, 1}           (Context - giờ giải lao / chuyển tiết)

Không gian hành động 7 chiều MultiDiscrete([3, 3, 7, 12, 2, 5, 2]):
  [0] channel     : 0..2  -> 5GHz [36, 40, 44] | 2.4GHz [1, 6, 11]
  [1] bandwidth   : 0..2  -> [HE20, HE40, HE80]
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

# =====================================================================================
# 1. HẰNG SỐ KIẾN TRÚC (đồng bộ 100% với deploy_hil_agent.py & real_router_env.py)
# =====================================================================================
OBS_LOW = np.array([-90.0, -105.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
OBS_HIGH = np.array([-30.0, -50.0, 300.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
STATE_NAMES = [
    "avg_rssi", "noise_floor", "sta_count", "retry_rate",
    "p_c", "airtime_util", "ofdma_ratio", "is_break_time"
]

ACTION_NVEC = [3, 3, 7, 12, 2, 5, 2]
ACTION_NAMES = ["channel", "bandwidth", "tx_power", "mcs_ceiling", "beacon", "obss_pd", "beamform"]

CHANNELS_5G = [36, 40, 44]
CHANNELS_2G = [1, 6, 11]
BANDWIDTHS = ["HE20", "HE40", "HE80"]
TX_POWER_DBM = np.array([14, 15, 16, 17, 18, 19, 20], dtype=np.float64)
BEACON_INTS = [80, 300]
OBSS_PD_DBM = np.array([-82, -77, -72, -67, -62], dtype=np.float64)
OBSS_PD_OFFSETS = [0, 5, 10, 15, 20]  # he_spr_non_srg_obss_pd_max_offset (so với -82 dBm)

QOE_LATENCY_MS = 15.0
QOE_LOSS = 0.01


def piecewise_lr_schedule(initial_value: float = 3.5e-4, min_lr: float = 2.5e-5) -> Callable[[float], float]:
    """Lịch trình giảm tốc độ học có sàn chặn dưới (Floor-clamped Power Decay)."""
    def schedule(progress_remaining: float) -> float:
        decayed = initial_value * (max(float(progress_remaining), 0.0) ** 1.35)
        return float(max(min_lr, decayed))
    return schedule


class NormalizedWifiFeaturesExtractor(BaseFeaturesExtractor):
    """
    Bộ trích xuất đặc trưng chuẩn hóa Min-Max nội tuyến về [-1.0, 1.0] cho vector trạng thái 8 chiều:
    [avg_rssi, noise_floor, sta_count, retry_rate, p_c, airtime_util, ofdma_ratio, is_break_time].
    """
    def __init__(self, observation_space: spaces.Box):
        super().__init__(observation_space, features_dim=64)
        self.register_buffer("low", torch.tensor(OBS_LOW, dtype=torch.float32))
        self.register_buffer("high", torch.tensor(OBS_HIGH, dtype=torch.float32))
        self.net = nn.Sequential(
            nn.Linear(8, 64),
            nn.LayerNorm(64),
            nn.SiLU(),
            nn.Linear(64, 64),
            nn.LayerNorm(64),
            nn.SiLU(),
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        norm_obs = 2.0 * (observations - self.low) / (self.high - self.low + 1e-6) - 1.0
        norm_obs = torch.clamp(norm_obs, -1.0, 1.0)
        return self.net(norm_obs)


# Alias tương thích ngược
CrossLayerNormExtractor = NormalizedWifiFeaturesExtractor


# =====================================================================================
# 2. BẢN SAO SỐ VẬT LÝ NHÂN QUẢ 4 TẦNG (ULTIMATE DIGITAL TWIN ENV)
# =====================================================================================
class UltimateDigitalTwinEnv(gym.Env):
    """
    Bản sao số Vật lý Nhân quả Chuẩn IEEE 802.11ax (Causal Physics-Driven Digital Twin):
    1. Mô hình hóa chính xác Chồng lấp Phổ tần khi Gộp kênh (Channel Bonding HE20/HE40/HE80).
    2. Tính toán chuỗi nhân quả 4 tầng:
       Môi trường RF + Tải + Hành động A_t
       -> Phổ kênh Bonding + SINR + PHY Rate + Va chạm CSMA/CA (p_c)
       -> Goodput (Bảo toàn lưu lượng) + Hàng đợi AQM/AQL + Trễ P99 + Loss
       -> Reward R_t.
    3. Liên kết nhân quả trạng thái kế tiếp: (s_t, a_t) -> s_{t+1} (bao gồm cả noise_on_chosen_ch).
    4. Dùng chung 100% lõi vật lý giữa step(), simulate_future() và compute_optimal_targets().
    """
    metadata = {"render_modes": []}

    MCS_BASE_RATE_20MHZ = np.array(
        [17.2, 34.4, 51.6, 68.8, 103.2, 137.6, 154.9, 172.1, 206.5, 229.4, 258.1, 286.8],
        dtype=np.float32,
    )
    MCS_REQ_SINR_DB = np.array(
        [5.0 + 2.2 * m for m in range(12)],
        dtype=np.float32,
    )

    def __init__(self, hardware_type: str = "WIFI_6", aqm_enabled: bool = True,
                 max_steps: int = 300, seed: int = GLOBAL_SEED):
        super().__init__()
        self.hardware_type = hardware_type.upper()
        self.aqm_enabled = aqm_enabled
        self.max_steps = max_steps
        self.rng = np.random.default_rng(seed)

        self.action_space = spaces.MultiDiscrete([3, 3, 7, 12, 2, 5, 2])
        self.observation_space = spaces.Box(low=OBS_LOW.copy(), high=OBS_HIGH.copy(), dtype=np.float32)

        self.current_step = 0
        self.previous_action = None
        self.scenario_mode = 0
        self.current_obs = None

    @staticmethod
    def _dbm_sum(dbm_a: float, dbm_b: float) -> float:
        """Cộng công suất nhiễu theo thang tuyến tính mW rồi quy đổi ngược về dBm."""
        mw = (10.0 ** (dbm_a / 10.0)) + (10.0 ** (dbm_b / 10.0))
        return float(10.0 * np.log10(max(mw, 1e-12)))

    @classmethod
    def _classify_regime(cls, obs: np.ndarray) -> Dict[str, Any]:
        """
        Phân tách chính xác Nhiễu RF (Tầng PHY + MAC) và Nghẽn Tranh chấp (Tầng MAC + OFDMA):
        - Phải nhìn kết hợp cả noise_obs & ofdma_obs (PHY Preamble Sensing) cùng pc_obs, retry_obs, sta, airtime_obs (MAC Sensing).
        """
        rssi, noise_obs, sta, retry_obs, pc_obs, airtime_obs, ofdma_obs, is_break = [
            float(x) for x in obs
        ]
        is_roaming_storm = (sta > 80.0)
        # Bão UDP Jammer: STA không đông nhưng Airtime cao và khung ngắn làm sập tỷ lệ ghép kênh OFDMA (< 0.36)
        is_udp_storm = (sta <= 80.0) and (airtime_obs > 0.72) and (ofdma_obs < 0.36)
        is_heavy_congestion = is_roaming_storm or is_udp_storm
        is_medium_load = (not is_heavy_congestion) and (35.0 < sta <= 80.0)

        # Nhiễu RF Kép đè cả CH36 & CH40: Bắt buộc tầng PHY báo noise_obs > -72.0 dBm VÀ tầng MAC có va chạm lớn
        is_severe_rf = (noise_obs > -72.0) and (pc_obs >= 0.60 or retry_obs >= 0.22)
        # Nhiễu RF Đơn kênh tại CH36: Tầng PHY báo noise_obs trong (-84.0, -72.0] dBm VÀ tầng MAC có va chạm
        is_moderate_rf = (not is_severe_rf) and (
            ((noise_obs > -84.0) and (pc_obs >= 0.22 or retry_obs >= 0.14))
            or (pc_obs >= 0.85 and sta <= 75.0)
        )

        # Kênh tối ưu lý tưởng theo phổ nhiễu RF
        if is_severe_rf:
            ideal_ch = 2  # Kênh 44 (Ốc đảo sạch khi 36+40 bị nhiễu kép)
            sub_noise_dbm = np.array(
                [max(noise_obs, -65.0), max(noise_obs - 1.5, -67.0), -95.5],
                dtype=np.float32,
            )
        elif is_moderate_rf:
            ideal_ch = 1  # Kênh 40 (Sơ tán đơn kênh khi 36 bị nhiễu)
            sub_noise_dbm = np.array(
                [max(noise_obs, -76.0), -95.5, -85.0],
                dtype=np.float32,
            )
        else:
            ideal_ch = 0  # Kênh 36 (Kênh chuẩn sạch nhất khi không có nhiễu RF ngoại lai)
            sub_noise_dbm = np.array([-96.5, -94.0, -92.5], dtype=np.float32)

        # Băng thông tối ưu theo áp lực tranh chấp MAC + chồng lấp phổ Channel Bonding
        if is_heavy_congestion:
            ideal_bw = 0  # HE20: Nén miền va chạm + tăng +6dB PSD chống Bão Roaming / Bão UDP
        elif is_medium_load or is_moderate_rf or is_severe_rf:
            ideal_bw = 1  # HE40: Cân bằng dung lượng 40MHz và tránh chồng phổ 80MHz vào kênh nhiễu
        else:
            ideal_bw = 2  # HE80: Bung toàn bộ 80MHz khi kênh sạch và tải thoáng (STA <= 35)

        # Công suất phát Tx Power tối ưu (0..6 -> 14..20 dBm)
        ideal_tx = int(np.clip(np.round((-44.0 - rssi) / 5.5 - (0.5 if sta > 100.0 else 0.0)), 0, 6))

        # Beacon Interval tối ưu theo ngữ cảnh giờ giải lao (is_break_time)
        ideal_bcn = 0 if int(round(is_break)) == 1 else 1

        # Beamforming tối ưu: Bật (1) để tăng +2.8dB SINR; tắt (0) khi quá đông STA (>110) và retry cao để tránh overhead sounding
        ideal_bf = 0 if (sta > 110.0 and retry_obs > 0.48) else 1

        # Ngưỡng OBSS-PD tối ưu (-82..-62 dBm -> idx 0..4) điều khiển trực tiếp bởi cảm biến PHY ofdma_obs
        if ofdma_obs < 0.20:
            ideal_obss = 0
        elif ofdma_obs < 0.34:
            ideal_obss = 1
        elif ofdma_obs < 0.50:
            ideal_obss = 2
        elif ofdma_obs < 0.72:
            ideal_obss = 3
        else:
            ideal_obss = 4

        # Tính SINR chuẩn tại cấu hình tối ưu để xác định trần MCS chuẩn xác theo Sigmoid
        opt_sub_noise = float(sub_noise_dbm[ideal_ch])
        if ideal_bw == 0:
            opt_eff_noise = opt_sub_noise - 2.0
        elif ideal_bw == 1:
            if ideal_ch == 1:
                opt_eff_noise = cls._dbm_sum(sub_noise_dbm[1], float(sub_noise_dbm[0]) - 15.0) + 1.0
            else:
                opt_eff_noise = opt_sub_noise + 1.5
        else:
            bonded_all = cls._dbm_sum(cls._dbm_sum(sub_noise_dbm[0], sub_noise_dbm[1]), sub_noise_dbm[2])
            opt_eff_noise = bonded_all + 2.5

        opt_tx_dbm = 14.0 + float(ideal_tx)
        opt_bf_gain = 2.8 if ideal_bf == 1 else 1.2
        if is_moderate_rf or is_severe_rf:
            fading_margin_db = 2.0 + 2.0 * (sta / 100.0)
        else:
            fading_margin_db = (
                (5.0 if is_heavy_congestion else (2.5 if is_medium_load else 0.0))
                + 5.5 * max(0.0, 0.55 - ofdma_obs)
            )
        opt_sinr_db = (rssi + 0.65 * (opt_tx_dbm - 17.0) + opt_bf_gain) - opt_eff_noise - fading_margin_db
        ideal_mcs = int(np.clip(np.searchsorted(cls.MCS_REQ_SINR_DB, opt_sinr_db - 3.8, side="right") - 1, 0, 11))

        return {
            "is_roaming_storm": is_roaming_storm,
            "is_udp_storm": is_udp_storm,
            "is_heavy_congestion": is_heavy_congestion,
            "is_medium_load": is_medium_load,
            "is_severe_rf": is_severe_rf,
            "is_moderate_rf": is_moderate_rf,
            "sub_noise_dbm": sub_noise_dbm,
            "fading_margin_db": fading_margin_db,
            "ideal_action": np.array(
                [ideal_ch, ideal_bw, ideal_tx, ideal_mcs, ideal_bcn, ideal_obss, ideal_bf],
                dtype=np.int64,
            ),
        }

    @classmethod
    def evaluate_network_physics(
        cls,
        obs: np.ndarray,
        action: np.ndarray,
        prev_action: np.ndarray = None,
        aqm_enabled: bool = True,
    ) -> Dict[str, float]:
        """
        HÀM LÕI VẬT LÝ NHÂN QUẢ 4 TẦNG DUY NHẤT (SINGLE SOURCE OF TRUTH):
        Tính toán đầy đủ các đại lượng mạng từ trạng thái quan sát 8D (obs) và hành động 7D (action).
        """
        try:
            rssi, noise_obs, sta, retry_obs, pc_obs, airtime_obs, ofdma_obs, is_break = [
                float(x) for x in obs
            ]
            ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_idx, bf_idx = [
                int(x) for x in action
            ]

            reg = cls._classify_regime(obs)
            sub_noise_dbm = reg["sub_noise_dbm"]
            is_heavy_congestion = reg["is_heavy_congestion"]
            is_medium_load = reg["is_medium_load"]
            is_severe_rf = reg["is_severe_rf"]
            is_moderate_rf = reg["is_moderate_rf"]
            fading_margin_db = reg["fading_margin_db"]
            ideal_ch, ideal_bw, ideal_tx, ideal_mcs, ideal_bcn, ideal_obss, ideal_bf = [
                int(x) for x in reg["ideal_action"]
            ]

            chosen_sub_noise_dbm = float(sub_noise_dbm[ch_idx])

            # 2. Tầng 1 -> Tầng 2: Mô hình Chồng lấp Phổ tần khi Gộp kênh (Channel Bonding Spectrum Overlap)
            if bw_idx == 0:  # HE20 (20 MHz): Tách biệt hoàn toàn giữa 36, 40, 44 (PSD Gain +6 dB)
                eff_noise_dbm = chosen_sub_noise_dbm - 2.0
                bw_tonal_factor = 1.00
            elif bw_idx == 1:  # HE40 (40 MHz): Khối {36, 40} hoặc {44, 48} (+3 dB noise)
                if ch_idx == 0:
                    eff_noise_dbm = cls._dbm_sum(sub_noise_dbm[0], sub_noise_dbm[1]) + 1.5
                elif ch_idx == 1:
                    sec_leak_dbm = float(sub_noise_dbm[0]) - 15.0
                    eff_noise_dbm = cls._dbm_sum(sub_noise_dbm[1], sec_leak_dbm) + 1.0
                else:
                    eff_noise_dbm = float(sub_noise_dbm[2]) + 1.5
                bw_tonal_factor = 2.08
            else:  # HE80 (80 MHz): Trải toàn bộ khối UNII-1 (36+40+44+48) -> Dính nhiễu của mọi kênh con bị đè!
                bonded_all = cls._dbm_sum(cls._dbm_sum(sub_noise_dbm[0], sub_noise_dbm[1]), sub_noise_dbm[2])
                primary_pen = 0.0 if ch_idx == 0 else (1.8 if ch_idx == 1 else 2.8)
                eff_noise_dbm = bonded_all + 2.5 + primary_pen
                bw_tonal_factor = 4.05

            # 3. Ngân sách Đường truyền (Link Budget: Tx Power + Beamforming + OBSS-PD)
            tx_power_dbm = 14.0 + float(tx_idx)  # 14..20 dBm
            bf_gain_db = 2.8 if (bf_idx == 1 and ideal_bf == 1) else (1.2 if (bf_idx == 0 and ideal_bf == 0) else -1.5)
            rssi_compensated = rssi + 0.65 * (tx_power_dbm - 17.0) + max(0.0, bf_gain_db)

            tx_mismatch_db = abs(tx_idx - ideal_tx) * 0.90
            obss_mismatch_db = abs(obss_idx - ideal_obss) * 1.15

            eff_sinr_db = float(
                rssi_compensated - eff_noise_dbm - fading_margin_db - tx_mismatch_db - obss_mismatch_db + min(0.0, bf_gain_db)
            )

            # 4. Tỷ lệ Lỗi Khung tầng PHY (PER) theo đường cong Sigmoid chuẩn IEEE 802.11ax
            req_sinr_db = float(cls.MCS_REQ_SINR_DB[mcs_idx])
            mcs_overreach = max(0, mcs_idx - ideal_mcs)
            mcs_underreach = max(0, ideal_mcs - mcs_idx)
            per_sigmoid = float(1.0 / (1.0 + np.exp(0.85 * (eff_sinr_db - req_sinr_db))))
            per_phy = float(np.clip(per_sigmoid + 0.06 * (mcs_overreach ** 2), 0.002, 0.98))
            mcs_mismatch_pen = 0.035 * mcs_underreach + 0.065 * mcs_overreach

            # 5. Mô hình Va chạm CSMA/CA (Bianchi DCF) kết hợp trực tiếp OFDMA_obs và Độ rộng Băng thông
            if is_heavy_congestion:
                bw_contention_factor = 0.45 if bw_idx == 0 else (1.65 if bw_idx == 1 else 2.45)
            elif is_medium_load or is_moderate_rf or is_severe_rf:
                bw_contention_factor = 1.05 if bw_idx == 0 else (0.55 if bw_idx == 1 else 1.75)
            else:
                bw_contention_factor = 1.35 if bw_idx == 0 else (0.95 if bw_idx == 1 else 0.30)

            channel_interf_ratio = float(np.clip((eff_noise_dbm - (-95.0)) / 32.0, 0.01, 0.90))
            ofdma_deficit = max(0.0, 0.65 - ofdma_obs)
            if (is_moderate_rf or is_severe_rf) and (ch_idx == ideal_ch) and (bw_idx <= 1):
                load_pressure = float(np.clip((sta / 145.0) + 0.18 * airtime_obs, 0.04, 0.65))
                effective_ofdma_obs = max(ofdma_obs, 0.62)
            else:
                load_pressure = float(np.clip((sta / 135.0) + 0.45 * airtime_obs + 0.40 * ofdma_deficit, 0.04, 1.30))
                effective_ofdma_obs = ofdma_obs

            ofdma_efficiency = float(
                np.clip(
                    0.52 * effective_ofdma_obs
                    + 0.36 * (1.0 - 0.18 * bw_idx)
                    + (0.08 if bf_idx == ideal_bf else -0.06)
                    - 0.25 * channel_interf_ratio,
                    0.08,
                    0.95,
                )
            )
            mac_contention = load_pressure * bw_contention_factor * (1.0 - 0.38 * ofdma_efficiency)

            p_c = float(
                np.clip(
                    0.42 * channel_interf_ratio
                    + 0.42 * mac_contention
                    + 0.42 * per_phy
                    + mcs_mismatch_pen
                    + 0.03 * abs(obss_idx - ideal_obss),
                    0.02,
                    0.98,
                )
            )
            retry_rate = float(np.clip(0.55 * p_c + 0.45 * per_phy, 0.01, 0.98))

            # 6. Tầng 2 -> Tầng 3: Service Capacity, Offered Load & Bảo toàn Lưu lượng Goodput
            beacon_overhead = 0.045 if beacon_idx == 0 else 0.015
            roaming_efficiency = 1.0 if beacon_idx == ideal_bcn else 0.88
            ofdma_tput_boost = 0.88 + 0.25 * ofdma_efficiency

            effective_mcs_rate = 0.25 * float(cls.MCS_BASE_RATE_20MHZ[mcs_idx]) + 0.12 * 137.6
            phy_rate_mbps = effective_mcs_rate * bw_tonal_factor * roaming_efficiency * ofdma_tput_boost
            service_capacity_mbps = float(
                max(8.0, phy_rate_mbps * (1.0 - beacon_overhead) * (1.0 - p_c) * (1.0 - 0.60 * per_phy))
            )

            mcs_scale = 0.84 * float((0.25 * float(cls.MCS_BASE_RATE_20MHZ[ideal_mcs]) + 0.12 * 137.6) / 88.212)

            if (not is_heavy_congestion) and (not is_medium_load) and (not is_moderate_rf) and (not is_severe_rf):
                offered_load_mbps = float(np.clip(195.0 + sta * 1.2 + 25.0 * airtime_obs, 205.0, 245.0) * mcs_scale)
            elif is_medium_load or is_moderate_rf or is_severe_rf:
                offered_load_mbps = float(np.clip(98.0 + sta * 0.35 + 15.0 * airtime_obs, 105.0, 132.0) * mcs_scale)
            else:
                offered_load_mbps = float(np.clip(26.0 + sta * 0.04 + 6.0 * airtime_obs, 30.0, 38.5) * mcs_scale)

            # ĐỊNH LUẬT BẢO TOÀN LƯU LƯỢNG: Goodput = min(Offered_Load, Service_Capacity)
            goodput_mbps = float(min(offered_load_mbps, service_capacity_mbps))
            airtime_next = float(np.clip(offered_load_mbps / max(service_capacity_mbps, 10.0), 0.08, 0.99))

            # 7. Mô hình Hàng đợi M/G/1 + AQM (FQ-CoDel + Adaptive AQL 1200us) -> Trễ P99 & Loss
            rho = offered_load_mbps / max(service_capacity_mbps, 8.0)
            overload_ratio = max(0.0, (offered_load_mbps - service_capacity_mbps) / max(offered_load_mbps, 1.0))

            if aqm_enabled:
                ofdma_queue_relief = 1.0 - 0.24 * ofdma_efficiency
                queue_delay_ms = (1.1 + 2.4 * min(rho, 1.0) + (26.0 * min(rho - 1.0, 2.5) if rho > 1.02 else 0.0)) * ofdma_queue_relief
            else:
                queue_delay_ms = 4.5 + 18.0 * (min(rho, 3.5) ** 2) + (38.0 * max(0.0, rho - 0.85))

            retrans_delay_ms = 1.20 * (retry_rate / max(1.0 - retry_rate, 0.16))
            mean_latency_ms = 1.65 + queue_delay_ms + retrans_delay_ms
            jitter_ms = 0.12 + 1.45 * (p_c ** 2) + (1.85 * overload_ratio if aqm_enabled else 9.5 * overload_ratio)
            p99_latency_ms = float(mean_latency_ms + 2.33 * jitter_ms)

            mac_exhaust_loss = (p_c * per_phy) ** 3
            congestion_drop = (0.065 * overload_ratio) if aqm_enabled else (0.58 * overload_ratio)
            packet_loss_ratio = float(np.clip(mac_exhaust_loss + congestion_drop, 0.0, 1.0))
            packet_loss_pct = packet_loss_ratio * 100.0

            # 8. Tầng 3 -> Tầng 4: Goodput + Hàng đợi AQM/AQL + Trễ P99 + Loss -> Reward R_t
            goodput_fulfillment = goodput_mbps / max(offered_load_mbps, 1.0)
            tput_score = 1.80 * goodput_fulfillment + 0.65 * (goodput_mbps / 100.0)
            penalty_collision = 3.8 * (p_c ** 2)
            penalty_latency = 0.035 * min(p99_latency_ms, 60.0) + 1.15 * float(np.tanh(max(0.0, p99_latency_ms - 15.0) / 18.0))
            penalty_loss = 3.0 * packet_loss_ratio + (0.55 if packet_loss_pct >= 1.0 else 0.0)
            sla_bonus = 0.55 if (p99_latency_ms <= 15.0 and packet_loss_pct < 1.0) else -0.35
            beacon_bonus = 0.30 if (beacon_idx == ideal_bcn) else -0.15

            weapon_shaping = (
                - 0.45 * (0.0 if ch_idx == ideal_ch else 1.0)
                - 0.85 * abs(bw_idx - ideal_bw)
                - 0.25 * abs(tx_idx - ideal_tx)
                - 0.16 * abs(mcs_idx - ideal_mcs)
                - 0.42 * abs(obss_idx - ideal_obss)
                - 0.30 * abs(bf_idx - ideal_bf)
            )

            penalty_action_flapping = 0.0
            if prev_action is not None and (not is_heavy_congestion) and (not is_severe_rf) and (not is_moderate_rf):
                if int(prev_action[0]) == 0 and ch_idx != int(prev_action[0]):
                    penalty_action_flapping -= 0.45
                if int(prev_action[1]) == 2 and bw_idx != int(prev_action[1]):
                    penalty_action_flapping -= 0.25

            reward = float(
                tput_score
                - penalty_collision
                - penalty_latency
                - penalty_loss
                + sla_bonus
                + beacon_bonus
                + weapon_shaping
                + penalty_action_flapping
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
                "p99_latency_ms": p99_latency_ms,
                "jitter_ms": jitter_ms,
                "packet_loss_pct": packet_loss_pct,
            }
        except Exception as exc:
            logging.error(f"Lỗi trong evaluate_network_physics: {exc}")
            return {
                "reward": -5.0, "throughput": 10.0, "goodput": 10.0, "offered_load": 50.0,
                "p_c": 0.9, "retry_rate": 0.5, "per_phy": 0.5, "eff_sinr_db": 5.0,
                "eff_rssi_dbm": -75.0, "eff_noise_dbm": -70.0, "chosen_sub_noise_dbm": -70.0,
                "airtime_next": 0.9, "ofdma_next": 0.2, "p99_latency_ms": 50.0,
                "jitter_ms": 10.0, "packet_loss_pct": 25.0,
            }

    @classmethod
    def compute_optimal_targets(cls, obs: np.ndarray, aqm_enabled: bool = True) -> Tuple[np.ndarray, Dict[str, float]]:
        """
        HÀM TÌM NGHIỆM TỐI ƯU TOÀN CỤC (SIMULATOR ORACLE):
        Trả về tổ hợp hành động 7 chiều tối ưu nhất và bộ chỉ số vật lý tương ứng cho trạng thái obs.
        """
        reg = cls._classify_regime(obs)
        ideal_act = reg["ideal_action"]
        best_metrics = cls.evaluate_network_physics(obs, ideal_act, aqm_enabled=aqm_enabled)
        return ideal_act.copy(), best_metrics

    def _sample_ambient_context(self) -> np.ndarray:
        """Sinh bối cảnh môi trường theo chương trình học phân tầng 4 pha (Stratified Curriculum)."""
        phase = (self.current_step // 25) % 4
        self.scenario_mode = phase
        rssi = float(self.rng.uniform(-88.0, -38.0))

        if phase == 0:  # Bình yên: CH36 sạch, tải thoáng (STA <= 34) -> CH36 + HE80 tối ưu
            noise = float(self.rng.uniform(-97.0, -87.0))
            sta = float(self.rng.uniform(4.0, 33.0))
            retry = float(self.rng.uniform(0.02, 0.11))
            pc = float(self.rng.uniform(0.03, 0.16))
            airtime = float(self.rng.uniform(0.12, 0.42))
            ofdma = float(self.rng.uniform(0.62, 0.90))
            is_break = 0.0 if self.rng.random() < 0.75 else 1.0
        elif phase == 1:  # Nhiễu RF đơn kênh tại CH36 (noise trong [-82.5, -73.5] dBm) -> CH40 + HE40 tối ưu
            noise = float(self.rng.uniform(-82.5, -73.5))
            sta = float(self.rng.uniform(12.0, 68.0))
            retry = float(self.rng.uniform(0.16, 0.34))
            pc = float(self.rng.uniform(0.28, 0.56))
            airtime = float(self.rng.uniform(0.42, 0.66))
            ofdma = float(self.rng.uniform(0.38, 0.60))
            is_break = 0.0 if self.rng.random() < 0.70 else 1.0
        elif phase == 2:  # Bão Roaming (>85 STA) hoặc Bão UDP Jammer (ofdma < 0.32) trên kênh sạch -> CH36 + HE20 tối ưu
            noise = float(self.rng.uniform(-95.0, -86.5))
            if self.rng.random() < 0.58:
                sta = float(self.rng.uniform(86.0, 210.0))
                ofdma = float(self.rng.uniform(0.10, 0.30))
                is_break = 1.0 if self.rng.random() < 0.85 else 0.0
            else:
                sta = float(self.rng.uniform(4.0, 24.0))
                ofdma = float(self.rng.uniform(0.08, 0.26))
                is_break = 0.0 if self.rng.random() < 0.75 else 1.0
            retry = float(self.rng.uniform(0.38, 0.65))
            pc = float(self.rng.uniform(0.42, 0.72))
            airtime = float(self.rng.uniform(0.75, 0.96))
        else:  # Nhiễu RF kép đè cả CH36 và CH40 (noise > -71.5 dBm, pc >= 0.65) -> CH44 + HE40 tối ưu
            noise = float(self.rng.uniform(-70.5, -56.0))
            sta = float(self.rng.uniform(10.0, 72.0))
            retry = float(self.rng.uniform(0.24, 0.42))
            pc = float(self.rng.uniform(0.66, 0.95))
            airtime = float(self.rng.uniform(0.40, 0.68))
            ofdma = float(self.rng.uniform(0.12, 0.34))
            is_break = 1.0 if self.rng.random() < 0.50 else 0.0

        return np.array([rssi, noise, sta, retry, pc, airtime, ofdma, is_break], dtype=np.float32)

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
        metrics = self.evaluate_network_physics(
            self.current_obs, action, prev_action=self.previous_action, aqm_enabled=self.aqm_enabled
        )
        self.previous_action = np.array(action, copy=True)

        # Liên kết nhân quả (s_t, a_t) -> s_{t+1}:
        # Phản ánh kết quả thực thi hành động a_t vào cả 6 chỉ số động (bao gồm chiều thứ 2 là noise_on_chosen_ch),
        # đồng thời duy trì dấu vết bối cảnh vật lý của pha hiện tại để chống hiện tượng ping-pong nhảy ngược về kênh nhiễu.
        ambient_next = self._sample_ambient_context()
        same_phase = ((self.current_step // 25) % 4) == (((self.current_step - 1) // 25) % 4)
        if same_phase:
            phase = (self.current_step // 25) % 4
            raw_noise_blend = 0.78 * ambient_next[1] + 0.22 * np.clip(metrics["chosen_sub_noise_dbm"], -100.0, -55.0)
            raw_retry_blend = 0.80 * ambient_next[3] + 0.20 * metrics["retry_rate"]
            raw_pc_blend = 0.80 * ambient_next[4] + 0.20 * metrics["p_c"]
            raw_air_blend = 0.80 * ambient_next[5] + 0.20 * metrics["airtime_next"]
            raw_ofdma_blend = 0.80 * ambient_next[6] + 0.20 * metrics["ofdma_next"]

            if phase == 0:
                noise_next = float(np.clip(raw_noise_blend, -98.0, -86.5))
                retry_next = float(np.clip(raw_retry_blend, 0.02, 0.13))
                pc_next = float(np.clip(raw_pc_blend, 0.03, 0.19))
                air_next = float(np.clip(raw_air_blend, 0.10, 0.46))
                ofdma_next = float(np.clip(raw_ofdma_blend, 0.58, 0.92))
            elif phase == 1:
                noise_next = float(np.clip(raw_noise_blend, -83.0, -73.0))
                retry_next = float(np.clip(raw_retry_blend, 0.15, 0.36))
                pc_next = float(np.clip(raw_pc_blend, 0.26, 0.58))
                air_next = float(np.clip(raw_air_blend, 0.38, 0.68))
                ofdma_next = float(np.clip(raw_ofdma_blend, 0.36, 0.62))
            elif phase == 2:
                noise_next = float(np.clip(raw_noise_blend, -96.0, -85.5))
                retry_next = float(np.clip(raw_retry_blend, 0.36, 0.68))
                pc_next = float(np.clip(raw_pc_blend, 0.40, 0.74))
                air_next = float(np.clip(raw_air_blend, 0.74, 0.97))
                ofdma_next = float(np.clip(raw_ofdma_blend, 0.08, 0.32))
            else:
                noise_next = float(np.clip(raw_noise_blend, -71.0, -55.0))
                retry_next = float(np.clip(raw_retry_blend, 0.22, 0.45))
                pc_next = float(np.clip(raw_pc_blend, 0.64, 0.96))
                air_next = float(np.clip(raw_air_blend, 0.38, 0.70))
                ofdma_next = float(np.clip(raw_ofdma_blend, 0.10, 0.35))

            next_obs = np.array(
                [
                    float(np.clip(0.80 * ambient_next[0] + 0.20 * metrics["eff_rssi_dbm"], -88.0, -38.0)),
                    noise_next,
                    ambient_next[2],
                    retry_next,
                    pc_next,
                    air_next,
                    ofdma_next,
                    ambient_next[7],
                ],
                dtype=np.float32,
            )
        else:
            next_obs = ambient_next

        self.current_obs = next_obs
        terminated = bool(self.current_step >= self.max_steps)
        return next_obs.copy(), float(metrics["reward"]), terminated, False, metrics

    def simulate_future(self, current_real_state: np.ndarray, action: np.ndarray) -> float:
        """HÀM TIÊN TRI (SIMULATOR ORACLE): Dùng chung 100% lõi evaluate_network_physics với hàm step()."""
        metrics = self.evaluate_network_physics(
            current_real_state, action, prev_action=None, aqm_enabled=self.aqm_enabled
        )
        return float(metrics["reward"])

    def oracle_action(self, current_real_state: np.ndarray) -> np.ndarray:
        """Hành động tối ưu 7 chiều theo bản sao số."""
        opt_act, _ = self.compute_optimal_targets(current_real_state, aqm_enabled=self.aqm_enabled)
        return opt_act


# =====================================================================================
# 3. LỚP BỌC ĐA HÌNH TỰ NHẬN THỨC PHẦN CỨNG (HARDWARE-AWARE WRAPPER)
# =====================================================================================
class HardwareAwareWrapper(gym.ActionWrapper):
    """
    Lớp Bọc Đa Hình Tự Nhận Thức Phần Cứng (Polymorphic Hardware-Aware Action Wrapper):
    - Hoạt động trực tiếp trên không gian 7 chiều MultiDiscrete([3, 3, 7, 12, 2, 5, 2]) của PPO.
    - Đóng góp trực tiếp vào quá trình huấn luyện PPO ở Bước 3 và triển khai thực tế:
      + WIFI_6 (IEEE 802.11ax - Xiaomi AX3000T / MediaTek MT7981B): Mở toàn bộ 7 chiều vũ khí RRM.
      + WIFI_5_AC (IEEE 802.11ac): Hỗ trợ VHT20/40/80, khóa trần VHT-MCS 0..9, tắt OBSS-PD (idx=0), giữ Beamforming.
      + WIFI_4_LEGACY (IEEE 802.11n - Router cũ mượn đi thi): Khóa băng thông tối đa HT40 (bw<=1),
        khóa trần HT-MCS 0..7 (mcs<=7), tắt OBSS-PD (idx=0) và tắt Beamforming (bf=0).
    """
    SUPPORTED_PROFILES = {
        "WIFI_6": {
            "max_bw_idx": 2,       # HE20, HE40, HE80
            "max_mcs_idx": 11,     # HE-MCS 0..11 (1024-QAM)
            "allow_obss_pd": True, # Spatial Reuse (-82..-62 dBm)
            "allow_beamform": True,# SU/MU Beamforming
            "active_weapons": ["channel", "bandwidth", "tx_power", "mcs_ceiling", "beacon_int", "obss_pd", "beamforming"],
        },
        "WIFI_5_AC": {
            "max_bw_idx": 2,       # VHT20, VHT40, VHT80
            "max_mcs_idx": 9,      # VHT-MCS 0..9 (256-QAM)
            "allow_obss_pd": False,
            "allow_beamform": True,
            "active_weapons": ["channel", "bandwidth", "tx_power", "mcs_ceiling", "beacon_int", "beamforming"],
        },
        "WIFI_4_LEGACY": {
            "max_bw_idx": 1,       # Chỉ HT20, HT40 (Không có 80MHz)
            "max_mcs_idx": 7,      # Chỉ HT-MCS 0..7 (64-QAM)
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
                print(f"[+] Đã phát hiện Router Wi-Fi 6 (802.11ax MediaTek). Kích hoạt toàn bộ 7/7 vũ khí RRM.")
            elif self.hardware_type == "WIFI_5_AC":
                print(f"[!] Đã phát hiện Router Wi-Fi 5 (802.11ac). Khóa MCS<=9 và cố định OBSS-PD mặc định.")
            else:
                print(f"[!] CẢNH BÁO: Đã phát hiện Router Wi-Fi 4 (802.11n Legacy). Kích hoạt chế độ Fallback (BW<=40MHz, MCS<=7, tắt OBSS-PD & Beamforming).")

    def set_hardware_profile(self, hardware_type: str):
        self.hardware_type = hardware_type.upper()
        self.profile = self.SUPPORTED_PROFILES[self.hardware_type]
        if hasattr(self.env, "hardware_type"):
            self.env.hardware_type = self.hardware_type

    def action(self, action: np.ndarray) -> np.ndarray:
        """Ánh xạ và kẹp an toàn vector 7 chiều từ PPO sang giới hạn vật lý của phần cứng thực tế."""
        act = np.array(action, dtype=np.int64, copy=True).reshape(-1)
        act[0] = int(np.clip(act[0], 0, 2))
        act[1] = int(np.clip(act[1], 0, self.profile["max_bw_idx"]))
        act[2] = int(np.clip(act[2], 0, 6))
        act[3] = int(np.clip(act[3], 0, self.profile["max_mcs_idx"]))
        act[4] = int(np.clip(act[4], 0, 1))
        act[5] = int(np.clip(act[5], 0, 4)) if self.profile["allow_obss_pd"] else 0
        act[6] = int(np.clip(act[6], 0, 1)) if self.profile["allow_beamform"] else 0
        return act

    def simulate_future(self, current_real_state: np.ndarray, action: np.ndarray) -> float:
        safe_act = self.action(action)
        return self.env.simulate_future(current_real_state, safe_act)


# Alias tương thích ngược cho Bước 1 trong Notebook
DigitalTwinEnv = UltimateDigitalTwinEnv
