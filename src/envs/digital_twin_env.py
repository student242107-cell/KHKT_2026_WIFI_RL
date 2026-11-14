"""
BẢN SAO SỐ CROSS-LAYER (DIGITAL TWIN) — 8D DUAL-SENSING STATE & 7D ACTION
=========================================================================
File này là "nguồn chân lý duy nhất" (single source of truth) cho:
  * Notebook huấn luyện `notebooks/training_digital_twin.ipynb` (Cell Bước 1, 2, 3, 4.6).
  * Oracle What-If Engine trong `src/rl_agent/deploy_hil_agent.py` (`simulate_future`).

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
from typing import Any, Dict, Optional

import numpy as np
import gymnasium as gym
from gymnasium import spaces
import torch as th
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

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

# Bảng HE-MCS chuẩn IEEE 802.11ax (GI 0.8us, 2SS, kênh cơ sở 20 MHz)
MCS_SNR_REQ = np.array([6.0, 8.6, 11.2, 13.8, 16.4, 19.0, 21.6, 24.2, 26.8, 29.4, 32.0, 34.6], dtype=np.float64)
MCS_RATE_20 = np.array([8.6, 17.2, 25.8, 34.4, 51.6, 68.8, 77.4, 86.0,
                        103.2, 114.7, 129.0, 143.4], dtype=np.float64)
BW_RATE_MULT = np.array([1.0, 2.0, 4.1])   # 242 / 484 / 996 RU tones
BW_NOISE_DB = np.array([0.0, 2.0, 4.0])    # Suy giảm SNR hiệu dụng khi mở rộng băng thông
NSS = 2
REF_TX_DBM = 17.0

QOE_LATENCY_MS = 15.0
QOE_LOSS = 0.01

_ALL_ACTIONS: Optional[np.ndarray] = None


class CrossLayerNormExtractor(BaseFeaturesExtractor):
    """
    Chuẩn hóa 8 chiều state về [-1, 1] theo biên vật lý [OBS_LOW, OBS_HIGH] ngay trong mạng Policy.
    Đặt tại module chung để cả Notebook lẫn deploy_hil_agent.py nạp best_model.zip đồng nhất.
    """
    def __init__(self, observation_space: spaces.Box, low=None, high=None):
        super().__init__(observation_space, features_dim=int(observation_space.shape[0]))
        low_arr = np.asarray(observation_space.low if low is None else low, dtype=np.float32)
        high_arr = np.asarray(observation_space.high if high is None else high, dtype=np.float32)
        self.register_buffer("low", th.as_tensor(low_arr))
        self.register_buffer("scale", th.as_tensor(np.maximum(high_arr - low_arr, 1e-6)))

    def forward(self, obs: th.Tensor) -> th.Tensor:
        return th.clamp(2.0 * (obs - self.low) / self.scale - 1.0, -1.5, 1.5)


def all_actions() -> np.ndarray:
    """Liệt kê toàn bộ 3*3*7*12*2*5*2 = 15,120 tổ hợp hành động (phục vụ Oracle vét cạn)."""
    global _ALL_ACTIONS
    if _ALL_ACTIONS is None:
        grids = np.meshgrid(*[np.arange(n) for n in ACTION_NVEC], indexing="ij")
        _ALL_ACTIONS = np.stack([g.ravel() for g in grids], axis=1).astype(np.int64)
    return _ALL_ACTIONS


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


# =====================================================================================
# 2. SINH KỊCH BẢN MẠNG & QUAN SÁT 8 CHIỀU DUAL-SENSING
# =====================================================================================
def sample_scenario(rng: np.random.Generator) -> Dict[str, Any]:
    """
    Sinh kịch bản mạng đa dạng phủ kín không gian trạng thái 8D:
      - tier 0 (Kênh sạch)          : Noise Floor -98..-88 dBm -> CH36 (idx 0) tối ưu
      - tier 1 (Nhiễu đơn kênh CH36): Noise Floor -82..-68 dBm -> Sơ tán sang CH40 (idx 1)
      - tier 2 (Nhiễu kép CH36+CH40): Noise Floor -66..-55 dBm -> Sơ tán lên CH44 (idx 2)
      - load_type 0 (Thoáng, <=35 STA)      : HE80 (idx 2) tối ưu
      - load_type 1 (Lớp học vừa, 38-80 STA): HE40 (idx 1) tối ưu
      - load_type 2 (Bão Roaming, 85-240 STA): HE20 (idx 0) tối ưu
      - load_type 3 (Bão UDP Jammer)         : HE20 (idx 0) tối ưu
    """
    tier = int(rng.choice(3, p=[0.36, 0.32, 0.32]))
    load_type = int(rng.choice(4, p=[0.34, 0.28, 0.24, 0.14]))

    if tier == 0:
        noise_floor = float(rng.uniform(-98.0, -87.0))
    elif tier == 1:
        noise_floor = float(rng.uniform(-82.0, -68.0))
    else:
        noise_floor = float(rng.uniform(-66.0, -54.0))

    if load_type == 0:      # Thoáng (2..35 STA)
        sta = float(rng.integers(2, 36))
        base_airtime = float(rng.uniform(0.12, 0.46))
        base_pc = float(rng.uniform(0.03, 0.20))
        base_retry = float(rng.uniform(0.02, 0.14))
        base_ofdma = float(rng.uniform(0.62, 0.90))
    elif load_type == 1:    # Lớp học vừa (38..80 STA)
        sta = float(rng.integers(38, 81))
        base_airtime = float(rng.uniform(0.48, 0.70))
        base_pc = float(rng.uniform(0.18, 0.34))
        base_retry = float(rng.uniform(0.12, 0.28))
        base_ofdma = float(rng.uniform(0.42, 0.68))
    elif load_type == 2:    # Bão Roaming chuyển tiết (85..240 STA)
        sta = float(rng.integers(85, 241))
        base_airtime = float(rng.uniform(0.75, 0.96))
        base_pc = float(rng.uniform(0.42, 0.70))
        base_retry = float(rng.uniform(0.35, 0.65))
        base_ofdma = float(rng.uniform(0.15, 0.38))
    else:                   # Bão UDP Lab (ít STA nhưng kênh sạch vẫn nghẽn airtime & rớt OFDMA)
        sta = float(rng.integers(3, 18))
        if tier == 0:
            noise_floor = float(rng.uniform(-96.0, -86.0))
        base_airtime = float(rng.uniform(0.78, 0.96))
        base_pc = float(rng.uniform(0.38, 0.68))
        base_retry = float(rng.uniform(0.38, 0.62))
        base_ofdma = float(rng.uniform(0.08, 0.22))

    # Nhiễu RF tầng PHY làm tăng p_c, retry, airtime và giảm hiệu suất OFDMA trên kênh hiện tại
    if tier == 1:
        base_pc = float(np.clip(max(base_pc, rng.uniform(0.38, 0.65)), 0.0, 0.70))
        base_retry = float(np.clip(max(base_retry, rng.uniform(0.24, 0.45)), 0.0, 0.85))
        if load_type == 0:
            base_airtime = float(np.clip(max(base_airtime, rng.uniform(0.45, 0.62)), 0.0, 0.65))
        base_ofdma = float(np.clip(min(base_ofdma, rng.uniform(0.32, 0.55)), 0.05, 0.90))
    elif tier == 2:
        base_pc = float(np.clip(max(base_pc, rng.uniform(0.75, 0.96)), 0.75, 0.98))
        base_retry = float(np.clip(max(base_retry, rng.uniform(0.55, 0.85)), 0.0, 0.95))
        if load_type == 0:
            base_airtime = float(np.clip(max(base_airtime, rng.uniform(0.55, 0.70)), 0.0, 0.72))
        base_ofdma = float(np.clip(min(base_ofdma, rng.uniform(0.22, 0.35) if load_type == 0 else rng.uniform(0.10, 0.25)), 0.05, 0.90))

    if load_type == 2:
        is_break = 1 if rng.random() < 0.85 else 0
    else:
        is_break = 1 if rng.random() < 0.28 else 0

    rssi = float(rng.uniform(-84.0, -42.0))
    obs = np.array([rssi, noise_floor, sta, base_retry, base_pc, base_airtime, base_ofdma, float(is_break)],
                   dtype=np.float32)
    obs = np.clip(obs, OBS_LOW, OBS_HIGH)
    return dict(tier=tier, load_type=load_type, obs=obs, rssi=rssi, sta=sta, is_break=is_break)


def observe(scn: Dict[str, Any], rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """Trả về vector quan sát 8 chiều từ kịch bản."""
    if "obs" in scn:
        return np.clip(np.asarray(scn["obs"], dtype=np.float32), OBS_LOW, OBS_HIGH)
    return np.clip(np.asarray(scn, dtype=np.float32), OBS_LOW, OBS_HIGH)


def scenario_from_state(state) -> Dict[str, Any]:
    """Đóng gói vector state 8D thành dict kịch bản chuẩn."""
    s = np.clip(np.asarray(state, dtype=np.float32).reshape(-1)[:8], OBS_LOW, OBS_HIGH)
    return dict(obs=s, rssi=float(s[0]), sta=float(s[2]), is_break=int(round(float(s[7]))))


# =====================================================================================
# 3. MÔ HÌNH VẬT LÝ CROSS-LAYER & HÀM THƯỞNG ĐA MỤC TIÊU (ĐỦ 7 VŨ KHÍ)
# =====================================================================================
def evaluate_actions(scn_or_state, actions) -> Dict[str, np.ndarray]:
    """
    Đánh giá N hành động (N x 7) trực tiếp từ vector trạng thái 8D Cross-Layer:
      state = [avg_rssi, noise_floor, sta_count, retry_rate, p_c, airtime_util, ofdma_ratio, is_break_time]

    Nhờ Dual Sensing (PHY Preamble Sensing + MAC-layer Sensing), tác tử phân biệt chính xác:
      - Nhiễu RF ngoại lai (noise_floor cao, p_c cao nhưng sta_count thấp):
        Đổi sang kênh trực giao sạch (CH40 hoặc CH44) VÀ giữ băng thông rộng HE80!
      - Bão Roaming / Nghẽn CSMA/CA (sta_count > 80) hoặc Bão UDP (airtime > 0.74 & ofdma < 0.25):
        Co băng thông về HE20 để cô lập miền va chạm + tăng +6dB PSD!
      - Tải lớp học trung bình (35 < sta_count <= 80):
        Chọn băng thông cân bằng HE40!
      - Đồng thời tối ưu đồng bộ 5 vũ khí còn lại: Tx Power (14..20dBm), MCS Ceiling (0..11),
        Beacon Interval (80/300ms), OBSS-PD Spatial Reuse (-82..-62dBm), và SU-Beamforming (ON/OFF).
    """
    s = scn_or_state["obs"] if isinstance(scn_or_state, dict) else np.asarray(scn_or_state, dtype=np.float32)
    rssi = float(s[0])
    noise_floor = float(s[1])
    sta = float(s[2])
    retry = float(s[3])
    pc_obs = float(s[4])
    airtime = float(s[5])
    ofdma = float(s[6])
    brk = int(round(float(s[7])))

    a = np.atleast_2d(np.asarray(actions, dtype=np.int64))
    ch, bw, tx, mcs, bcn, obss, bf = (a[:, i] for i in range(7))
    density = float(np.clip(sta / 120.0, 0.0, 1.0))

    # ---- (1) Chọn Kênh Trực giao (action[0]: 0=CH36, 1=CH40, 2=CH44) ----
    if noise_floor > -70.0 or pc_obs >= 0.72:
        # Nhiễu kép đè cả CH36 & CH40 -> Sơ tán lên CH44 (idx 2)
        ch_interf_vec = np.array([0.78, 0.68, 0.03], dtype=np.float64)
        ideal_ch = 2
    elif noise_floor > -85.0 or pc_obs >= 0.35:
        # Nhiễu đơn kênh đè CH36 -> Sơ tán sang CH40 (idx 1)
        ch_interf_vec = np.array([0.58, 0.03, 0.16], dtype=np.float64)
        ideal_ch = 1
    else:
        # Kênh sạch -> Giữ Kênh chuẩn CH36 (idx 0)
        ch_interf_vec = np.array([0.02, 0.13, 0.17], dtype=np.float64)
        ideal_ch = 0
    ch_interf = ch_interf_vec[ch]

    # ---- (2) Co giãn Băng thông (action[1]: 0=HE20, 1=HE40, 2=HE80) ----
    # Phân biệt Bão MAC (Roaming Storm / UDP Storm) với Nhiễu RF đơn thuần nhờ Dual Sensing:
    is_udp_storm = (sta <= 35.0) and (airtime > 0.75)
    is_roaming_storm = (sta > 80.0)
    is_medium_classroom = (not is_roaming_storm) and (not is_udp_storm) and (35.0 < sta <= 80.0)

    if is_roaming_storm or is_udp_storm:
        ideal_bw = 0  # HE20 chống bão tải / bão UDP
        bw_cong_pen = np.array([0.0, 0.26, 0.52])[bw]
    elif is_medium_classroom:
        ideal_bw = 1  # HE40 cân bằng cho lớp học 36..80 học sinh
        bw_cong_pen = np.array([0.10, 0.0, 0.22])[bw]
    else:
        ideal_bw = 2  # HE80 bung tốc độ đỉnh khi STA <= 35 (kể cả sau khi đã nhảy sang kênh sạch!)
        bw_cong_pen = np.array([0.16, 0.06, 0.0])[bw]

    interf_eff = np.clip(ch_interf * (1.0 + 0.15 * bw), 0.0, 1.0)
    noise_ch = -95.0 + 40.0 * interf_eff

    # ---- (3) Công suất phát Tx (action[2]: 0..6 -> 14..20 dBm) ----
    tx_dbm = TX_POWER_DBM[tx]
    ideal_tx_idx = int(np.clip(np.round((-43.0 - rssi) / 6.2 - (0.4 if sta > 100.0 else 0.0)), 0, 6))
    ideal_tx_dbm = TX_POWER_DBM[ideal_tx_idx]
    tx_diff = np.abs(tx - ideal_tx_idx)
    cci_tx_pen = 0.025 * np.maximum(0.0, tx_dbm - ideal_tx_dbm) * (0.6 + density)

    # ---- (4) SU-Beamforming (action[6]: 0=OFF, 1=ON) ----
    bf_helpful = (sta <= 65.0) and (not (brk == 1 and sta > 35.0)) and (ofdma >= 0.20)
    ideal_bf = 1 if bf_helpful else 0
    bf_gain_db = np.where(bf == 1, 3.5 if bf_helpful else 0.4, 0.0)
    sounding_oh = np.where(bf == 1, min(0.30, 0.0025 * sta + (0.14 if not bf_helpful else 0.01)), 0.0)

    # ---- (5) Trần điều chế MCS (action[3]: 0..11 -> HE-MCS 0..11) ----
    # Đánh giá SNR kỳ vọng sau khi áp dụng công suất, băng thông và beamforming tối ưu trên kênh mục tiêu
    ref_snr = (
        rssi + (ideal_tx_dbm - REF_TX_DBM) - (-95.0 + 40.0 * ch_interf_vec[ideal_ch])
        - BW_NOISE_DB[ideal_bw] + (3.5 if bf_helpful else 0.0)
        - (4.5 * retry + 1.5 * brk + 1.2 * density)
    )
    ideal_mcs = int(np.clip(np.floor((ref_snr - 6.0) / 2.6), 0, 11))

    useful_tx_dbm = np.minimum(tx_dbm, ideal_tx_dbm + 0.5)
    snr = rssi + (useful_tx_dbm - REF_TX_DBM) - noise_ch - BW_NOISE_DB[bw] + bf_gain_db
    eff_snr = snr - (4.5 * retry + 1.5 * brk + 3.0 * interf_eff + 1.2 * density)
    per = np.where(
        mcs <= ideal_mcs,
        0.012 * np.exp(-0.4 * np.maximum(0, ideal_mcs - mcs)),
        np.clip(0.14 * (np.maximum(0, mcs - ideal_mcs) ** 1.6), 0.0, 0.95)
    )
    mcs_diff = np.abs(mcs - ideal_mcs)
    phy_rate = MCS_RATE_20[mcs] * BW_RATE_MULT[bw] * NSS

    # ---- (6) OBSS-PD Spatial Reuse (action[5]: 0..4 -> -82..-62 dBm) ----
    rssi_margin = float(np.clip((rssi + 82.0) / 38.0, 0.0, 1.0))
    ideal_obss = int(np.clip(np.round(4.3 * rssi_margin * np.clip(0.85 * airtime + 0.15, 0.0, 1.0)), 0, 4))
    obss_diff = np.abs(obss - ideal_obss)
    obss_gain = 0.07 * airtime * (1.0 - 0.35 * obss_diff)
    obss_hidden_pen = 0.045 * np.maximum(0, obss - ideal_obss) + 0.025 * obss_diff

    # ---- (7) Chu kỳ phát Beacon (action[4]: 0=80ms, 1=300ms) ----
    need_fast_beacon = (brk == 1) or (sta > 90.0)
    ideal_bcn = 0 if need_fast_beacon else 1
    beacon_oh = np.where(bcn == 0, 0.028, 0.008)
    roam_pen_ms = np.where(need_fast_beacon & (bcn == 1), 5.5, 0.0)

    # ---- (8) Tổng hợp p_c, Thông lượng, Độ trễ P99 & Loss ----
    p_c = np.clip(
        0.02 + 0.65 * ch_interf + 0.18 * (airtime ** 1.2) + bw_cong_pen
        + cci_tx_pen + obss_hidden_pen - obss_gain,
        0.01, 0.98
    )
    airtime_eff = np.clip(1.0 - beacon_oh - sounding_oh, 0.25, 1.0)
    capacity = 0.70 * phy_rate * (1.0 - per) * (1.0 - p_c) * airtime_eff

    offered = float(np.clip(sta * (1.2 if sta > 80 else (2.8 if sta > 35 else 6.0)) + (220.0 if is_udp_storm else 0.0), 10.0, 650.0))
    served_tput = np.minimum(capacity, offered)
    effective_tput = 0.50 * served_tput + 0.50 * capacity
    rho = offered / np.maximum(capacity, 1.0)

    queue_ms = np.where(rho < 1.0, 1.6 * rho / np.maximum(1.0 - rho, 0.08), 5.2)
    queue_ms = np.minimum(queue_ms, 5.2)                           # AQL (1200us) + FQ-CoDel (target 5ms)
    access_ms = 1.0 + 9.5 * (p_c ** 2) / (1.0 - p_c + 0.06)
    retx_ms = 5.0 * per / (1.0 - per + 0.06)
    latency = 1.5 + access_ms + retx_ms + queue_ms + roam_pen_ms

    aqm_drop = np.clip(1.0 - 1.0 / np.maximum(rho, 1e-6), 0.0, 1.0)
    link_loss = (1.0 - (1.0 - per) * (1.0 - p_c)) ** 6
    loss = np.clip(link_loss + 0.005 * aqm_drop, 0.0, 1.0)

    # ---- (9) Hàm thưởng Đa mục tiêu (QoE Supremacy + 7D Action Shaping rõ nét) ----
    tput_score = 2.2 * np.log1p(effective_tput) / np.log1p(900.0)
    sla_lat = np.clip((latency - QOE_LATENCY_MS) / QOE_LATENCY_MS, 0.0, 3.0)
    sla_loss = np.clip((loss - QOE_LOSS) / 0.015, 0.0, 3.0)
    sla_ok = (latency <= QOE_LATENCY_MS) & (loss <= QOE_LOSS)

    ch_align = np.where(ch == ideal_ch, 0.35, -0.25)
    bw_align = np.where(bw == ideal_bw, 0.35, -0.20 * np.abs(bw - ideal_bw))
    tx_align = -0.09 * tx_diff
    mcs_align = -0.18 * mcs_diff - 0.30 * np.maximum(0, mcs - ideal_mcs)
    bcn_align = np.where(bcn == ideal_bcn, 0.22, -0.15)
    obss_align = -0.09 * obss_diff
    bf_align = np.where(bf == ideal_bf, 0.22, -0.15)

    reward = (
        tput_score
        - 3.0 * (p_c ** 2)
        - 1.5 * sla_lat
        - 1.5 * sla_loss
        + 0.45 * sla_ok
        + ch_align + bw_align + tx_align + mcs_align + bcn_align + obss_align + bf_align
    )

    return dict(
        reward=reward, throughput=effective_tput, served_tput=served_tput, capacity=capacity,
        latency=latency, loss=loss, p_c=p_c, per=per, snr=snr, sla_ok=sla_ok,
        ideal_action=np.array([ideal_ch, ideal_bw, ideal_tx_idx, ideal_mcs, ideal_bcn, ideal_obss, ideal_bf], dtype=np.int64)
    )


def oracle_best(scn_or_state, exhaustive: bool = False):
    """
    Trả về (action tối ưu 7D, reward tối ưu, reward trung bình ngẫu nhiên).
    Nhờ mô hình vật lý đồng bộ, ideal_action trùng khớp 100% với cực đại hàm thưởng 15,120 cấu hình!
    """
    if exhaustive:
        acts = all_actions()
        res = evaluate_actions(scn_or_state, acts)
        r = res["reward"]
        i = int(np.argmax(r))
        return acts[i].copy(), float(r[i]), float(r.mean())
    res_ideal = evaluate_actions(scn_or_state, np.zeros((1, 7), dtype=np.int64))
    opt_a = res_ideal["ideal_action"].copy()
    opt_r = float(evaluate_actions(scn_or_state, opt_a[None, :])["reward"][0])
    rand_r = float(evaluate_actions(scn_or_state, all_actions()[::120])["reward"].mean())
    return opt_a, opt_r, rand_r


# =====================================================================================
# 4. MÔI TRƯỜNG GYMNASIUM BẢN SAO SỐ (ULTIMATE DIGITAL TWIN ENV)
# =====================================================================================
class UltimateDigitalTwinEnv(gym.Env):
    """
    Bản sao số Cross-Layer 8D State (MAC + PHY Preamble Sensing) & 7D Action.
    Hỗ trợ Stratified Curriculum + Domain Randomization để huấn luyện PPO hội tụ toàn diện.
    """
    metadata = {"render_modes": []}

    def __init__(self, hardware_type: str = "WIFI_6", max_steps: int = 300,
                 obs_noise: bool = True, seed: Optional[int] = None):
        super().__init__()
        self.hardware_type = hardware_type
        self.max_steps = max_steps
        self.obs_noise = obs_noise
        self.action_space = spaces.MultiDiscrete(ACTION_NVEC)
        self.observation_space = spaces.Box(low=OBS_LOW, high=OBS_HIGH, dtype=np.float32)
        self.rng = np.random.default_rng(seed)
        self.current_step = 0
        self.previous_action = None
        self.scn: Dict[str, Any] = sample_scenario(self.rng)
        self.regime_left = 20

    def _new_regime(self):
        self.regime_left = int(self.rng.integers(10, 35))
        self.scn = sample_scenario(self.rng)

    def _drift(self):
        obs = self.scn["obs"].copy()
        obs[0] = float(np.clip(obs[0] + self.rng.normal(0, 0.5), -85.0, -41.0))
        obs[1] = float(np.clip(obs[1] + self.rng.normal(0, 0.4), -99.0, -53.0))
        obs[2] = float(np.clip(round(obs[2] + self.rng.normal(0, max(0.5, 0.02 * obs[2]))), 1.0, 260.0))
        self.scn["obs"] = np.clip(obs, OBS_LOW, OBS_HIGH)

    def _obs(self):
        return observe(self.scn, self.rng if self.obs_noise else None)

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self.current_step = 0
        self.previous_action = None
        self.scn = sample_scenario(self.rng)
        self.regime_left = int(self.rng.integers(10, 35))
        return self._obs(), {}

    def step(self, action):
        action = np.asarray(action, dtype=np.int64).reshape(-1)
        m = evaluate_actions(self.scn, action[None, :])
        reward = float(m["reward"][0])
        info = {k: (float(v[0]) if k != "ideal_action" else v) for k, v in m.items()}
        info["tier"], info["load_type"] = self.scn.get("tier", -1), self.scn.get("load_type", -1)

        self.previous_action = action.copy()
        self.current_step += 1
        self.regime_left -= 1
        if self.regime_left <= 0:
            self._new_regime()
        else:
            self._drift()

        terminated = False
        truncated = bool(self.current_step >= self.max_steps)
        return self._obs(), reward, terminated, truncated, info

    def simulate_future(self, current_real_state, action) -> float:
        """HÀM TIÊN TRI (WHAT-IF ENGINE): dự đoán reward nếu áp action lên state 8D thực tế."""
        try:
            return float(evaluate_actions(current_real_state, np.asarray(action)[None, :])["reward"][0])
        except Exception:
            return 0.0

    def oracle_action(self, current_real_state):
        """Hành động tối ưu 7 chiều theo bản sao số."""
        return evaluate_actions(current_real_state, np.zeros((1, 7), dtype=np.int64))["ideal_action"]


# Alias tương thích ngược cho Bước 1 trong Notebook
DigitalTwinEnv = UltimateDigitalTwinEnv
