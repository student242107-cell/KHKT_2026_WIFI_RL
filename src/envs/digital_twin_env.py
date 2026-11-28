"""
BẢN SAO SỐ VẬT LÝ LIÊN TỤC 4 TẦNG (DIGITAL TWIN) — 8D DUAL-SENSING STATE & 7D ACTION (9 KÊNH NON-DFS)
=====================================================================================================
Single Source of Truth cho:
  * Notebook huấn luyện `notebooks/training_digital_twin.ipynb` (Bước 1..4.6)
  * Triển khai thực tế & Fine-tuning (`src/rl_agent/deploy_hil_agent.py`, `src/rl_agent/train.py`)

Kiến trúc chuẩn hóa (Phương án 1 - Pure Continuous-Markov POMDP 8D + VecFrameStack(4)):
  1. Không gian trạng thái 8 chiều (`noise_floor` = `noise_on_chosen_ch` khớp `iw survey dump [in use]`):
     [avg_rssi, noise_floor, sta_count, retry_rate, p_c, airtime_util, ofdma_ratio, is_break_time]
  2. Chuỗi Markov Vật lý Liên tục (KHÔNG còn Contextual Bandits, KHÔNG còn đồng hồ 25 bước,
     KHÔNG còn kéo 70% về `ambient_anchor`, KHÔNG còn thiên vị UNII-1 / CH36):
     - Trường nhiễu 9 kênh thực `true_ch_noise[0..8]` tiến hóa liên tục qua Ornstein-Uhlenbeck
       với thời lượng đợt nhiễu/tải ngẫu nhiên (15..55 bước).
     - `noise_next` cập nhật nhân quả trực tiếp theo kênh `action[0]` vừa chọn (`noise_on_chosen_ch`).
     - Các biến tầng MAC (`retry_rate`, `p_c`, `airtime_util`, `ofdma_ratio`) tiến hóa 75% từ `metrics`.
  3. Giải pháp triệt tiêu bẫy quên 4 bước của 8D POMDP:
     - `VecFrameStack(n_stack=4)` nắm bắt động học chuyển biến ngắn hạn 4 chu kỳ liên tiếp.
     - `StatefulChannelGuardWrapper` (Channel-Hold khi kênh sạch & Quarantine tạm thời kênh vừa nhiễu)
       đồng bộ nhất quán giữa Digital Twin và OpenWrt HIL Controller (`deploy_hil_agent.py`).
"""
import math
import logging
from collections import deque
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
import gymnasium as gym
from gymnasium import spaces
import torch
import torch.nn as nn
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

GLOBAL_SEED = 2026
N_FRAME_STACK = 4
DT_STEP_SEC = 10.0
OU_THETA_RF = 0.105        # s^-1 -> exp(-theta * dt) = 0.3499 (alpha_rf ≈ 0.65)
OU_SIGMA_STAT_RF = 1.25    # dB stationary std within a Markov regime
OU_THETA_RSSI = 0.0223     # s^-1 -> exp(-theta * dt) = 0.8001
OU_SIGMA_STAT_RSSI = 1.35  # dB stationary std for RSSI
OU_THETA_STA = 0.0198      # s^-1 -> exp(-theta * dt) = 0.8204
OU_SIGMA_STAT_STA = 3.10   # STA stationary std

OBS_LOW = np.array([-90.0, -105.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
OBS_HIGH = np.array([-30.0, -50.0, 300.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
STATE_NAMES = [
    "avg_rssi", "noise_floor", "sta_count", "retry_rate",
    "p_c", "airtime_util", "ofdma_ratio", "is_break_time",
]

NON_DFS_5G_CHANNELS = [36, 40, 44, 48, 149, 153, 157, 161, 165]
CHANNEL_FREQ_MHZ = np.array([5180, 5200, 5220, 5240, 5745, 5765, 5785, 5805, 5825], dtype=np.float32)
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
    """Ánh xạ an toàn (ch_idx, bw_idx) sang kênh 5GHz Non-DFS và cụm bonding IEEE 802.11ax hợp lệ."""
    ch_idx_safe = int(np.clip(ch_idx, 0, len(NON_DFS_5G_CHANNELS) - 1))
    bw_idx_safe = int(np.clip(bw_idx, 0, 2))
    primary_ch = NON_DFS_5G_CHANNELS[ch_idx_safe]
    if primary_ch == 165 and bw_idx_safe > 0:
        return 165, "HE20", (165,), True
    if bw_idx_safe == 0:
        return primary_ch, "HE20", (primary_ch,), False
    if bw_idx_safe == 1:
        return primary_ch, "HE40", BONDING_GROUP_40MHZ[primary_ch], False
    return primary_ch, "HE80", BONDING_GROUP_80MHZ[primary_ch], False


def measure_mt76_survey_noise(spectrum_noise_9d: np.ndarray, ch_idx: int, bw_idx: int) -> float:
    """
    Mô hình hóa phép đo nhiễu khảo sát của driver OpenWrt `mt76` (`iw survey dump [in use]`):
      - Ưu tiên năng lượng nhiễu IPI trên Kênh chính 20 MHz (`primary_ch`).
      - Khi gộp HE40/HE80, tính thêm ghép xuyên băng gốc suy giảm -4.5 dB từ kênh phụ mạnh nhất.
    """
    ch_safe = int(np.clip(ch_idx, 0, len(NON_DFS_5G_CHANNELS) - 1))
    _, actual_htmode, occ_subs, _ = resolve_valid_5g_channel_and_bw(ch_safe, bw_idx)
    pri_n = float(spectrum_noise_9d[ch_safe])
    if actual_htmode == "HE20":
        return pri_n
    sec_vals = [
        float(spectrum_noise_9d[NON_DFS_5G_CHANNELS.index(c)])
        for c in occ_subs
        if NON_DFS_5G_CHANNELS.index(c) != ch_safe
    ]
    if not sec_vals:
        return pri_n
    sec_max = max(sec_vals)
    mw = (10.0 ** (pri_n / 10.0)) + (10.0 ** ((sec_max - 4.5) / 10.0))
    return float(10.0 * math.log10(mw if mw > 1e-12 else 1e-12))


def gamma_p99_from_moments(mean_lat: float, jitter: float, d_min: float = 1.50) -> float:
    """
    Tính phân vị thứ 99 chính xác của phân phối Gamma dịch chuyển X = d_min + Gamma(k, theta)
    thông qua xấp xỉ Wilson-Hilferty (khớp < 0.1% so với scipy.stats.gamma.ppf(0.99), không cần scipy).
    """
    excess_mean = max(0.05, float(mean_lat) - d_min)
    sigma = max(0.04, float(jitter))
    shape_k = max(0.25, (excess_mean / sigma) ** 2.0)
    z99 = 2.326347874
    wh_term = max(0.15, 1.0 - 1.0 / (9.0 * shape_k) + z99 / (3.0 * math.sqrt(shape_k)))
    return float(d_min + excess_mean * (wh_term ** 3.0))


def piecewise_lr_schedule(initial_value: float = 3.5e-4, min_lr: float = 2.5e-5) -> Callable[[float], float]:
    """Lịch trình giảm tốc độ học có sàn chặn dưới (Floor-clamped Power Decay)."""
    def schedule(progress_remaining: float) -> float:
        decayed = initial_value * (max(float(progress_remaining), 0.0) ** 1.35)
        return float(max(min_lr, decayed))
    return schedule


def build_stacked_obs(obs_history: Any, n_stack: int = N_FRAME_STACK) -> np.ndarray:
    """Xếp chồng `n_stack` khung 8D (zero-pad bên trái nếu thiếu, khớp 100% VecFrameStack SB3)."""
    if isinstance(obs_history, np.ndarray) and obs_history.ndim == 1 and obs_history.shape[0] == 8 * n_stack:
        return obs_history.astype(np.float32)
    frames = list(obs_history) if not (isinstance(obs_history, np.ndarray) and obs_history.ndim == 1) else [obs_history]
    frames = [np.asarray(f, dtype=np.float32).reshape(8) for f in frames[-n_stack:]]
    while len(frames) < n_stack:
        frames.insert(0, np.zeros(8, dtype=np.float32))
    return np.concatenate(frames, axis=0).astype(np.float32)


class NormalizedWifiFeaturesExtractor(BaseFeaturesExtractor):
    """
    Bộ trích xuất đặc trưng chuẩn hóa Min-Max về [-1.0, 1.0] kết hợp:
      1) Khung trạng thái hiện tại `obs_t` (8D cuối) mở rộng qua lưới Radial Basis Functions (RBF 8 tâm đều
         trên [-1, 1] theo Sutton & Barto Ch. 9.5) giúp mạng phân giải sắc nét các nấc lượng tử hóa bậc thang.
      2) Nhánh động học đa khung 32D (`temporal_net`) khai thác trọn vẹn cả 4 khung `t-3..t`:
         - Vận tốc tức thời `vel_1 = f_t - f_{t-1}` (8D)
         - Gia tốc biến thiên `acc_2 = f_t - 2*f_{t-1} + f_{t-2}` (8D)
         - Độ trôi toàn cửa sổ `drift_3 = f_t - f_{t-3}` (8D)
         - Trung bình trượt nền 4 bước `mean_4 = 0.25 * (f_{t-3} + f_{t-2} + f_{t-1} + f_t)` (8D)
    """
    def __init__(self, observation_space: spaces.Box):
        super().__init__(observation_space, features_dim=64)
        obs_dim = int(observation_space.shape[0])
        self.obs_dim = obs_dim
        self.register_buffer("low8", torch.tensor(OBS_LOW.copy(), dtype=torch.float32))
        self.register_buffer("high8", torch.tensor(OBS_HIGH.copy(), dtype=torch.float32))
        self.register_buffer("rbf_centers", torch.linspace(-1.0, 1.0, 8, dtype=torch.float32).view(1, 1, 8))
        # Đầu vào cur_net: 8 (linear) + 8*8 (uniform RBF) = 72 chiều
        self.cur_skip = nn.Linear(72, 64)
        self.cur_net = nn.Sequential(
            nn.Linear(72, 128),
            nn.SiLU(),
            nn.Linear(128, 128),
            nn.SiLU(),
            nn.Linear(128, 64),
        )
        self.temporal_net = nn.Sequential(
            nn.Linear(32, 64),
            nn.SiLU(),
            nn.Linear(64, 64),
        )

    def _norm8(self, x8: torch.Tensor) -> torch.Tensor:
        n = 2.0 * (x8 - self.low8) / (self.high8 - self.low8 + 1e-6) - 1.0
        return torch.clamp(n, -1.0, 1.0)

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        cur_raw = observations[:, -8:]
        cur_norm = self._norm8(cur_raw)
        diff = cur_norm.unsqueeze(-1) - self.rbf_centers
        rbf_feat = torch.exp(-10.0 * (diff * diff)).reshape(cur_norm.shape[0], 64)
        cur_full = torch.cat([cur_norm, rbf_feat], dim=-1)
        out = self.cur_skip(cur_full) + self.cur_net(cur_full)
        if observations.shape[1] >= 32:
            f0 = self._norm8(observations[:, -32:-24])
            f1 = self._norm8(observations[:, -24:-16])
            f2 = self._norm8(observations[:, -16:-8])
            f3 = cur_norm
            vel_1 = f3 - f2
            acc_2 = f3 - 2.0 * f2 + f1
            drift_3 = f3 - f0
            mean_4 = 0.25 * (f0 + f1 + f2 + f3)
            kin_32 = torch.cat([vel_1, acc_2, drift_3, mean_4], dim=-1)
            out = out + 0.25 * self.temporal_net(kin_32)
        elif observations.shape[1] >= 16:
            f_prev = self._norm8(observations[:, -16:-8])
            vel_1 = cur_norm - f_prev
            zeros8 = torch.zeros_like(cur_norm)
            mean_4 = 0.25 * (3.0 * f_prev + cur_norm)
            kin_32 = torch.cat([vel_1, zeros8, vel_1, mean_4], dim=-1)
            out = out + 0.25 * self.temporal_net(kin_32)
        return out


CrossLayerNormExtractor = NormalizedWifiFeaturesExtractor


class HardwareAwareWrapper(gym.ActionWrapper):
    """Lớp Bọc Đa Hình Tự Nhận Thức Phần Cứng (Wi-Fi 6 / Wi-Fi 5 AC / Wi-Fi 4 Legacy)."""
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
        if hasattr(self.env.unwrapped, "hardware_type"):
            self.env.unwrapped.hardware_type = self.hardware_type
        if verbose:
            if self.hardware_type == "WIFI_6":
                print("[+] Đã phát hiện Router Wi-Fi 6 (802.11ax MediaTek). Kích hoạt toàn bộ 7/7 vũ khí RRM (9 kênh Non-DFS).")
            elif self.hardware_type == "WIFI_5_AC":
                print("[!] Đã phát hiện Router Wi-Fi 5 (802.11ac). Khóa MCS<=9 và cố định OBSS-PD mặc định.")
            else:
                print("[!] CẢNH BÁO: Đã phát hiện Router Wi-Fi 4 (802.11n Legacy). Fallback (BW<=40MHz, MCS<=7, tắt OBSS-PD & BF).")

    def set_hardware_profile(self, hardware_type: str):
        self.hardware_type = hardware_type.upper()
        self.profile = self.SUPPORTED_PROFILES[self.hardware_type]
        if hasattr(self.env.unwrapped, "hardware_type"):
            self.env.unwrapped.hardware_type = self.hardware_type

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
        return self.env.unwrapped.simulate_future(current_real_state, safe_act)


class StatefulChannelGuardWrapper(gym.Wrapper):
    """
    Lớp Bảo vệ Trạng thái Kênh & Cách ly Nhiễu (Stateful POMDP Hysteresis & Tabu Guardrail):
    - Đọc đúng `current_obs` mà Agent quan sát được (tuyệt đối KHÔNG nhìn trộm `true_current_obs` khi `obs_mask_phy=True`).
    - Triệt tiêu hoàn toàn bẫy "mất trí nhớ sau 4 bước" của chính sách 8D + VecFrameStack(4):
      1) Khi kênh hiện tại đang sạch (`noise <= -84.0 dBm`) và không sập MAC nghiêm trọng (`p_c < 0.66` hoặc `retry < 0.60`),
         giữ ổn định kênh hiện tại thay vì nhảy ngược về CH36 (Zero Ping-Pong), vẫn để PPO tự do chỉnh 6 chiều còn lại.
      2) Ngoại lệ: cho phép rời kênh lẻ 165 nếu tải nhẹ cần mở rộng băng thông HE40/HE80 trên kênh khác.
      3) Khi kênh hiện tại bị nhiễu (`noise > -84.0 dBm`) hoặc sập MAC kéo dài qua ngưỡng dwell,
         mở cổng để PPO thoát kênh và đưa kênh vừa hỏng vào `quarantine` trong `quarantine_steps` bước.
    """
    _ESCAPE_ROTATION = (4, 0, 6, 2, 5, 1, 7, 3, 8)
    _global_escape_cursor = 0

    def __init__(
        self,
        env: gym.Env,
        quarantine_steps: int = 12,
        hold_noise_thresh: float = -84.0,
        hold_pc_thresh: float = 0.66,
        hold_retry_thresh: float = 0.60,
        min_dwell_steps: int = 2,
    ):
        super().__init__(env)
        self.quarantine_steps = int(quarantine_steps)
        self.hold_noise_thresh = float(hold_noise_thresh)
        self.hold_pc_thresh = float(hold_pc_thresh)
        self.hold_retry_thresh = float(hold_retry_thresh)
        self.min_dwell_steps = int(min_dwell_steps)
        self.step_count = 0
        self.last_switch_step = -100
        self.current_ch = 0
        self.escape_cursor = StatefulChannelGuardWrapper._global_escape_cursor
        self.quarantine_until: Dict[int, int] = {}

    def reset(self, **kwargs):
        obs, info = self.env.reset(**kwargs)
        self.step_count = 0
        self.last_switch_step = -100
        self.current_ch = int(getattr(self.env.unwrapped, "current_ch", 0))
        self.quarantine_until = {}
        return obs, info

    def _quarantine_channel_and_cluster(self, ch_idx: int, noise_obs: float, bw_idx: int = 1) -> None:
        """Cách ly nhân quả kênh đang nhiễu và cụm gộp 40/80 MHz lân cận khi phát hiện nhiễu."""
        self.quarantine_until[ch_idx] = self.step_count + self.quarantine_steps
        if (noise_obs > -82.0 or bw_idx >= 1) and ch_idx < 8:
            partner_40 = ch_idx ^ 1
            self.quarantine_until[partner_40] = max(
                self.quarantine_until.get(partner_40, 0),
                self.step_count + max(6, self.quarantine_steps - 3),
            )
        if noise_obs > -72.0 and ch_idx < 8:
            block_range = range(0, 4) if ch_idx < 4 else range(4, 8)
            exp_block = self.step_count + max(8, self.quarantine_steps - 2)
            for c_b in block_range:
                self.quarantine_until[c_b] = max(self.quarantine_until.get(c_b, 0), exp_block)

    def _pick_rotated_escape_channel(
        self, allowed: list, proposed_ch: int, bw_req: int, sta_obs: float
    ) -> int:
        for _ in range(len(self._ESCAPE_ROTATION)):
            cand = self._ESCAPE_ROTATION[self.escape_cursor % len(self._ESCAPE_ROTATION)]
            self.escape_cursor = (self.escape_cursor + 1) % len(self._ESCAPE_ROTATION)
            StatefulChannelGuardWrapper._global_escape_cursor = self.escape_cursor
            if cand in allowed and cand != self.current_ch:
                if cand == 8 and bw_req > 0 and sta_obs <= 72.0 and (self.escape_cursor % 3 != 0):
                    wide_alt = [c for c in allowed if c < 8 and c != self.current_ch]
                    if wide_alt:
                        continue
                return cand
        allowed_other = [c for c in allowed if c != self.current_ch] or allowed
        return allowed_other[0]

    def filter_channel_action(self, obs_seen: np.ndarray, action: np.ndarray) -> Tuple[np.ndarray, bool]:
        act = np.array(action, dtype=np.int64, copy=True).reshape(-1)
        proposed_ch = int(np.clip(act[0], 0, len(NON_DFS_5G_CHANNELS) - 1))
        bw_cur = int(np.clip(act[1], 0, 2))
        noise_obs = float(obs_seen[1])
        sta_obs = float(obs_seen[2])
        retry_obs = float(obs_seen[3])
        pc_obs = float(obs_seen[4])
        ofdma_obs = float(obs_seen[6])

        # Xóa các kênh đã hết hạn cách ly
        for c in [k for k, exp in self.quarantine_until.items() if self.step_count >= exp]:
            self.quarantine_until.pop(c, None)

        dwell_elapsed = (self.step_count - self.last_switch_step) >= self.min_dwell_steps
        rf_jammed = (noise_obs > self.hold_noise_thresh)
        mac_collapsed = dwell_elapsed and (pc_obs >= self.hold_pc_thresh) and (retry_obs >= self.hold_retry_thresh)
        need_escape = rf_jammed or mac_collapsed
        stuck_on_ch165 = (self.current_ch == 8 and sta_obs <= 72.0 and ofdma_obs >= 0.35 and int(act[1]) > 0)
        held_channel = False

        if proposed_ch != self.current_ch:
            if (not need_escape) and (not stuck_on_ch165):
                act[0] = self.current_ch
                held_channel = True
            elif need_escape:
                self._quarantine_channel_and_cluster(self.current_ch, noise_obs, bw_cur)

        # Nếu đứng trên kênh đang nhiễu, cần rời CH165, hoặc đề xuất trúng kênh đang bị cách ly -> chọn kênh thoát xoay vòng
        if need_escape or stuck_on_ch165:
            if need_escape and (self.current_ch not in self.quarantine_until):
                self._quarantine_channel_and_cluster(self.current_ch, noise_obs, bw_cur)
            allowed = [c for c in range(len(NON_DFS_5G_CHANNELS)) if c not in self.quarantine_until]
            if not allowed:
                oldest_ch = min(self.quarantine_until.items(), key=lambda kv: kv[1])[0]
                self.quarantine_until.pop(oldest_ch, None)
                allowed = [oldest_ch]
            act[0] = self._pick_rotated_escape_channel(allowed, proposed_ch, int(act[1]), sta_obs)
        elif (not need_escape) and (act[0] in self.quarantine_until) and (act[0] != self.current_ch):
            # Bảo vệ không nhảy từ kênh sạch sang kênh vẫn đang trong thời hạn cách ly
            act[0] = self.current_ch
            held_channel = True

        if act[0] == 8 and int(act[1]) > 0:
            act[1] = 0
        return act, held_channel

    def step(self, action: np.ndarray):
        obs_seen = getattr(self.env.unwrapped, "current_obs", None)
        if obs_seen is not None:
            eff_act, held = self.filter_channel_action(obs_seen, action)
        else:
            eff_act, held = np.array(action, dtype=np.int64, copy=True), False

        prev_ch = self.current_ch
        obs, reward, terminated, truncated, info = self.env.step(eff_act)
        self.step_count += 1
        self.current_ch = int(eff_act[0])
        if self.current_ch != prev_ch:
            self.last_switch_step = self.step_count
        if isinstance(info, dict):
            info["guard_held_channel"] = held
            info["guard_effective_ch_idx"] = self.current_ch
            info["guard_quarantined_chs"] = sorted(list(self.quarantine_until.keys()))
        return obs, reward, terminated, truncated, info


class UltimateDigitalTwinEnv(gym.Env):
    """
    Bản sao số Vật lý Nhân quả Liên tục Chuẩn IEEE 802.11ax (Pure Continuous-Markov Physics MDP):
    - ZERO `ideal_action`, ZERO `weapon_shaping` L1, ZERO đồng hồ 25 bước, ZERO `ambient_anchor` kéo 70%.
    - Trường phổ 9 kênh thực `true_ch_noise[0..8]` biến thiên liên tục theo Chuỗi Markov + Ornstein-Uhlenbeck.
    - `obs[1]` là `noise_on_chosen_ch` (`true_ch_noise[current_ch]`), hoàn toàn nhân quả theo hành động chọn kênh.
    - `mcs_ceiling` vận hành như trần giới hạn thực sự trên bộ điều khiển Minstrel-HT nội tại.
    - Hỗ trợ `obs_mask_phy=True` để huấn luyện công bằng mô hình đối chứng `PPO MAC-Only`.
    """
    metadata = {"render_modes": []}

    MCS_BASE_RATE_20MHZ = np.array(
        [17.2, 34.4, 51.6, 68.8, 103.2, 137.6, 154.9, 172.1, 206.5, 229.4, 258.1, 286.8],
        dtype=np.float32,
    )
    MCS_REQ_SINR_DB = np.array([4.5 + 2.25 * m for m in range(12)], dtype=np.float32)
    # Suy hao truyền dẫn tự nhiên theo tần số Friis chuẩn: 20 * log10(f_MHz / 5180)
    FREQ_PATH_LOSS_DB = np.array(
        [0.0, 0.03, 0.07, 0.10, 0.90, 0.93, 0.96, 0.99, 1.02],
        dtype=np.float32,
    )
    _MCS_BASE_RATE_TUPLE = (17.2, 34.4, 51.6, 68.8, 103.2, 137.6, 154.9, 172.1, 206.5, 229.4, 258.1, 286.8)
    _MCS_REQ_SINR_TUPLE = tuple(4.5 + 2.25 * m for m in range(12))
    _EXP_NEG_095_REQ_TUPLE = tuple(math.exp(-0.95 * r) for r in _MCS_REQ_SINR_TUPLE)
    _FREQ_LOSS_TUPLE = (0.0, 0.03, 0.07, 0.10, 0.90, 0.93, 0.96, 0.99, 1.02)
    _CH_INDEX_MAP = {ch: idx for idx, ch in enumerate(NON_DFS_5G_CHANNELS)}
    _ORACLE_VALID_PAIRS = tuple((ch, bw) for ch in range(9) for bw in range(3) if not (ch == 8 and bw > 0))
    _ORACLE_P_CH = np.array([p[0] for p in _ORACLE_VALID_PAIRS], dtype=np.int64)
    _ORACLE_P_BW = np.array([p[1] for p in _ORACLE_VALID_PAIRS], dtype=np.int64)
    _ORACLE_P_TONAL = np.array([1.00 if bw == 0 else (2.04 if bw == 1 else 4.02) for _, bw in _ORACLE_VALID_PAIRS], dtype=np.float64)
    _ORACLE_P_FLOSS = (lambda f, vp: np.array([f[c] for c, _ in vp], dtype=np.float64))(_FREQ_LOSS_TUPLE, _ORACLE_VALID_PAIRS)
    _ORACLE_G_P, _ORACLE_G_BF, _ORACLE_G_OB, _ORACLE_G_TX = [
        a.ravel() for a in np.meshgrid(np.arange(25), np.arange(2), np.arange(5), np.arange(7), indexing="ij")
    ]
    _ORACLE_ROW_IDX = np.arange(1750, dtype=np.int64)
    _ORACLE_MCS_REQ = np.array(_MCS_REQ_SINR_TUPLE, dtype=np.float64)
    _ORACLE_MCS_RATE = np.array(_MCS_BASE_RATE_TUPLE, dtype=np.float64)

    @staticmethod
    def _clip_scalar(val: float, lo: float, hi: float) -> float:
        return lo if val < lo else (hi if val > hi else val)

    @staticmethod
    def _mcs_lookup(sinr_budget: float) -> int:
        if sinr_budget < 4.5:
            return 0
        idx = int((sinr_budget - 4.5 + 1e-9) / 2.25)
        return 11 if idx > 11 else idx

    def __init__(
        self,
        hardware_type: str = "WIFI_6",
        aqm_enabled: bool = True,
        max_steps: int = 300,
        seed: int = GLOBAL_SEED,
        obs_mask_phy: bool = False,
    ):
        super().__init__()
        self.hardware_type = hardware_type.upper()
        self.aqm_enabled = bool(aqm_enabled)
        self.max_steps = int(max_steps)
        self.obs_mask_phy = bool(obs_mask_phy)
        self.base_seed = int(seed)
        self.rng = np.random.default_rng(seed)

        self.action_space = spaces.MultiDiscrete(ACTION_NVEC)
        self.observation_space = spaces.Box(low=OBS_LOW.copy(), high=OBS_HIGH.copy(), dtype=np.float32)

        self.current_step = 0
        self.current_ch = 0
        self.previous_action: Optional[np.ndarray] = None
        self.scenario_mode = 0
        self.queue_backlog_mb = 0.0

        # Trạng thái vật lý ẩn liên tục của mô phỏng (không rò rỉ ra observation)
        self.true_ch_noise = np.full(9, -95.2, dtype=np.float32)
        self.target_ch_noise = np.full(9, -95.2, dtype=np.float32)
        self.target_rssi = -56.0
        self.target_sta = 18.0
        self.target_ofdma_base = 0.78
        self.target_demand_scale = 1.0
        self.current_is_break = 0.0
        self.regime_remaining_steps = 25

        self.true_current_obs: Optional[np.ndarray] = None
        self.current_obs: Optional[np.ndarray] = None

    @staticmethod
    def _dbm_sum(dbm_a: float, dbm_b: float) -> float:
        mw = (10.0 ** (dbm_a / 10.0)) + (10.0 ** (dbm_b / 10.0))
        return 10.0 * math.log10(mw if mw > 1e-12 else 1e-12)

    @staticmethod
    def _bessel_j0(x: float) -> float:
        """Xấp xỉ đa thức Bessel loại 1 bậc 0 J_0(x) (Abramowitz & Stegun 9.4.1 / 9.4.3, sai số < 1e-6)."""
        ax = abs(float(x))
        if ax < 3.0:
            y = (ax / 3.0) ** 2
            return (
                1.0
                - 2.2499997 * y
                + 1.2656208 * (y ** 2)
                - 0.3163866 * (y ** 3)
                + 0.0444479 * (y ** 4)
                - 0.0039444 * (y ** 5)
                + 0.0002100 * (y ** 6)
            )
        z = 3.0 / ax
        f0 = (
            0.79788456
            - 0.00000077 * z
            - 0.00552740 * (z ** 2)
            - 0.00009512 * (z ** 3)
            + 0.00137237 * (z ** 4)
            - 0.00072805 * (z ** 5)
            + 0.00014476 * (z ** 6)
        )
        theta0 = (
            ax
            - 0.78539816
            - 0.04166397 * z
            - 0.00003954 * (z ** 2)
            + 0.00262573 * (z ** 3)
            - 0.00054125 * (z ** 4)
            - 0.00029333 * (z ** 5)
            + 0.00013558 * (z ** 6)
        )
        return math.sqrt(1.0 / ax) * f0 * math.cos(theta0)

    @classmethod
    def _compute_jakes_bf_physics(
        cls,
        ch_idx: int,
        sta: float,
        retry_obs: float,
        airtime_obs: float,
        is_break: float,
    ) -> Tuple[float, float, float, bool]:
        """Mô hình lão hóa kênh CSI liên tục Clarke-Jakes theo tần số sóng mang & chi phí NDP Sounding."""
        ch_safe = 0 if ch_idx < 0 else (8 if ch_idx > 8 else int(ch_idx))
        lam = 299.792458 / float(CHANNEL_FREQ_MHZ[ch_safe])
        if int(round(is_break)) == 1:
            v_mps = 1.05 + 1.15 * cls._clip_scalar(sta / 150.0, 0.0, 1.30) + 0.55 * retry_obs
        else:
            v_mps = 0.12 + 0.42 * max(0.0, (sta - 75.0) / 110.0) + (
                0.95 * max(0.0, retry_obs - 0.26) if sta > 60.0 else 0.0
            )
        f_d = v_mps / lam
        dt_csi = 0.012 + 0.00028 * sta + 0.016 * airtime_obs
        x_dop = min(2.0 * math.pi * f_d * dt_csi, 3.8317)
        rho_csi = cls._bessel_j0(x_dop)
        bf_on_gain_db = 3.1 * rho_csi - 1.6 * max(0.0, 0.40 - rho_csi)
        bf_on_ovh = min(0.092, 0.006 + 0.00050 * sta + (0.012 if int(round(is_break)) == 1 else 0.0))
        is_csi_aging = (bf_on_gain_db - 26.0 * bf_on_ovh) < 0.0
        return float(rho_csi), float(bf_on_gain_db), float(bf_on_ovh), bool(is_csi_aging)

<<<<<<< HEAD
    @classmethod
    def _evaluate_minstrel_ht_amc(
        cls,
        eff_sinr_db: float,
        mcs_ceiling_idx: int,
        ewma_lag_db: float,
    ) -> Tuple[int, float, float]:
        """
        Mô hình hóa thuật toán Linux Minstrel-HT:
          - Tính bảng thông lượng kỳ vọng theo EWMA (prob kẹp [0.10, 0.90]), chọn r0, r1 và r_prob.
          - 85% khung thường chạy chuỗi 4-tầng Multi-Rate Retry (MRR [r0 -> r1 -> r_prob -> 0]).
          - 15% khung Lookaround thăm dò hướng lên trong dải (r0 + 1 .. mcs_ceiling_idx).
        """
        per_inst_list = []
        best_tp_est = -1.0
        r0 = 0
        r1 = 0
        r_prob = 0
        best_prob_score = -1.0

        e_inst_base = math.exp(cls._clip_scalar(0.95 * (eff_sinr_db - 1.8), -35.0, 65.0))
        e_ewma_base = math.exp(cls._clip_scalar(0.95 * (eff_sinr_db + ewma_lag_db - 1.8), -35.0, 65.0))
        exp_req_tuple = cls._EXP_NEG_095_REQ_TUPLE
        rate_tuple = cls._MCS_BASE_RATE_TUPLE

        for m in range(mcs_ceiling_idx + 1):
            exp_req_m = exp_req_tuple[m]
            rate_m = rate_tuple[m]
            per_inst = 1.0 / (1.0 + e_inst_base * exp_req_m)
            per_ewma = 1.0 / (1.0 + e_ewma_base * exp_req_m)
            per_inst_list.append(per_inst)

            p_succ_ewma = 1.0 - per_ewma
            tp_ewma = (min(0.90, p_succ_ewma) if p_succ_ewma >= 0.10 else 0.0) * rate_m
            if tp_ewma > best_tp_est:
                r1 = r0
                best_tp_est = tp_ewma
                r0 = m

            prob_score = p_succ_ewma * min(rate_m, 68.8)
            if prob_score > best_prob_score:
                best_prob_score = prob_score
                r_prob = m

        p0 = per_inst_list[r0]
        p1 = per_inst_list[r1]
        normal_rate = (
            (1.0 - p0) * cls._MCS_BASE_RATE_TUPLE[r0]
            + p0 * (1.0 - p1) * cls._MCS_BASE_RATE_TUPLE[r1]
            + p0 * p1 * cls._MCS_BASE_RATE_TUPLE[r_prob] * 0.65
        )
        normal_per = p0 * 0.58 + p0 * p1 * 0.42

        if mcs_ceiling_idx > r0:
            n_pr = float(mcs_ceiling_idx - r0)
            pr_rate_sum = 0.0
            pr_per_sum = 0.0
            for m in range(r0 + 1, mcs_ceiling_idx + 1):
                pm = per_inst_list[m]
                pr_rate_sum += cls._MCS_BASE_RATE_TUPLE[m] * (1.0 - pm)
                pr_per_sum += pm
            eff_mcs_rate = 0.36 * (0.85 * normal_rate + 0.15 * (pr_rate_sum / n_pr))
            per_phy = cls._clip_scalar(0.85 * normal_per + 0.15 * (pr_per_sum / n_pr), 0.002, 0.96)
        else:
            eff_mcs_rate = 0.36 * normal_rate
            per_phy = cls._clip_scalar(normal_per, 0.002, 0.96)

        return r0, float(eff_mcs_rate), float(per_phy)

    @staticmethod
    def _compute_kpi_reward(
        goodput_mbps: float,
        offered_load_mbps: float,
        service_capacity_mbps: float,
        p_c: float,
        per_phy: float,
        p99_latency_ms: float,
        packet_loss_pct: float,
        tx_power_dbm: float,
        is_invalid_bonding: bool = False,
        tx_slew_pen: float = 0.0,
    ) -> float:
        """Hàm mục tiêu 100% Pure Outcome KPI (không chứa heuristic target action shaping)."""
        packet_loss_ratio = packet_loss_pct / 100.0
        gp_fulfill = goodput_mbps / max(offered_load_mbps, 1.0)
        cap_headroom = 0.52 * math.log1p(service_capacity_mbps / max(offered_load_mbps, 25.0))
        energy_pen = 0.032 * (tx_power_dbm - 14.0)
        penalty_collision = 3.40 * (p_c * p_c) + 2.10 * per_phy
        penalty_latency = 0.042 * min(p99_latency_ms, 60.0) + 1.25 * math.tanh(max(0.0, p99_latency_ms - 15.0) / 15.0)
        penalty_loss = 3.20 * packet_loss_ratio + (0.60 if packet_loss_pct >= 1.0 else 0.0)
        sla_bonus = 0.60 if (p99_latency_ms <= 15.0 and packet_loss_pct < 1.0) else -0.40
        invalid_bond_pen = -0.80 if is_invalid_bonding else 0.0
        return float(
            2.60 * gp_fulfill
            + 0.65 * (goodput_mbps / 100.0)
            + cap_headroom
            - penalty_collision
            - penalty_latency
            - penalty_loss
            - energy_pen
            + sla_bonus
            + invalid_bond_pen
            - tx_slew_pen
        )

=======
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
    @classmethod
    def _construct_9ch_spectrum_profile(
        cls,
        obs: np.ndarray,
        rng: Optional[np.random.Generator] = None,
    ) -> np.ndarray:
        """
        Hàm fallback tái tạo hồ sơ phổ 9 kênh khi gọi đánh giá tĩnh trên một vector `obs` đơn lẻ
        (không truyền `sub_noise_dbm` thực từ simulator). Không thiên vị cứng CH36/CH149.
        """
        noise_obs = float(obs[1])
        sta = float(obs[2])
        airtime_obs = float(obs[5])
        base_thermal = np.array([-95.6, -95.4, -95.5, -95.3, -95.4, -95.2, -95.3, -95.1, -95.0], dtype=np.float32)

        if noise_obs <= -85.0:
            sub_noise = base_thermal.copy()
            if sta > 120.0 and airtime_obs > 0.85:
                sub_noise[0] += 1.5
            return sub_noise

        if rng is not None:
            # Nếu được cấp RNG (như trong bộ sinh kịch bản snapshot), đặt nhiễu trên một tập kênh tự nhiên bất kỳ
            sub_noise = base_thermal.copy()
            jam_idx = int(rng.integers(0, 9))
            sub_noise[jam_idx] = noise_obs
            return sub_noise

        # Fallback xác định khi gọi What-If tĩnh với một `obs` đơn lẻ
        sub_noise = base_thermal.copy()
        if noise_obs <= -74.0:
            sub_noise[0] = noise_obs
            sub_noise[1] = max(noise_obs - 2.0, -82.0)
        else:
            sub_noise[0:4] = np.clip(noise_obs - np.array([0.0, 0.8, 1.4, 2.0]), -76.0, -52.0)
        return sub_noise

    @classmethod
    def _original_evaluate_network_physics(
        cls,
        obs: np.ndarray,
        action: np.ndarray,
        prev_action: np.ndarray = None,
        aqm_enabled: bool = True,
        sub_noise_dbm: Optional[np.ndarray] = None,
        base_ofdma_capacity: Optional[float] = None,
        demand_scale: float = 1.0,
        queue_backlog_mb: float = 0.0,
    ) -> Dict[str, float]:
        """
        Lõi Tính toán Vật lý Viễn thông 4 Tầng Chuẩn IEEE 802.11ax (v3 Jury-Proof):
        1. Spectrum Bonding (tích hợp mW tuyến tính đúng 10log10(B)) & Bất đối xứng Primary/Secondary CCA.
        2. Link Budget thuần vật lý (LNA Overdrive), Clarke-Jakes Bessel J0 CSI Aging, OBSS-PD Spatial Reuse vs Hidden-Node Leak.
        3. Linux Minstrel-HT 4-tầng MRR + 15% Lookaround Probing & Va chạm CSMA/CA.
        4. Goodput IETF RFC 2647, Hàng đợi M/G/1 Pollaczek-Khinchine + Tồn dư đa bước Lindley & Wilson-Hilferty P99.
        5. Hàm thưởng 100% Pure Outcome KPI (không chứa target action shaping).
        """
        try:
            rssi = float(obs[0]); noise_obs = float(obs[1]); sta = float(obs[2]); retry_obs = float(obs[3])
            pc_obs = float(obs[4]); airtime_obs = float(obs[5]); ofdma_obs = float(obs[6]); is_break = float(obs[7])
            ch_idx = int(action[0]); bw_idx = int(action[1]); tx_idx = int(action[2]); mcs_ceiling_idx = int(action[3])
            beacon_idx = int(action[4]); obss_idx = int(action[5]); bf_idx = int(action[6])

            ch_idx = 0 if ch_idx < 0 else (8 if ch_idx > 8 else ch_idx)
            bw_idx = 0 if bw_idx < 0 else (2 if bw_idx > 2 else bw_idx)
            tx_idx = 0 if tx_idx < 0 else (6 if tx_idx > 6 else tx_idx)
            mcs_ceiling_idx = 0 if mcs_ceiling_idx < 0 else (11 if mcs_ceiling_idx > 11 else mcs_ceiling_idx)
            beacon_idx = 0 if beacon_idx < 0 else (1 if beacon_idx > 1 else beacon_idx)
            obss_idx = 0 if obss_idx < 0 else (4 if obss_idx > 4 else obss_idx)
            bf_idx = 0 if bf_idx < 0 else (1 if bf_idx > 1 else bf_idx)

            # 1. TẦNG VẬT LÝ PHỔ TẦN & GỘP KÊNH (TÍCH HỢP TUYẾN TÍNH mW CHUẨN 10log10(B))
            spectrum_noise = cls._construct_9ch_spectrum_profile(obs) if sub_noise_dbm is None else sub_noise_dbm

            chosen_sub_noise_dbm = float(spectrum_noise[ch_idx])
            primary_ch, actual_htmode, occupied_subs, is_invalid_bonding = resolve_valid_5g_channel_and_bw(ch_idx, bw_idx)
            ch_map = cls._CH_INDEX_MAP
            actual_bw_idx = 0 if actual_htmode == "HE20" else (1 if actual_htmode == "HE40" else 2)

            if actual_htmode == "HE20":
                occ_max_noise_dbm = chosen_sub_noise_dbm
                sec_max_noise_dbm = -100.0
<<<<<<< HEAD
                eff_noise_dbm = chosen_sub_noise_dbm
=======
                eff_noise_dbm = chosen_sub_noise_dbm - 2.2
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
                bw_tonal_factor = 1.00
            elif actual_htmode == "HE40":
                i0, i1 = ch_map[occupied_subs[0]], ch_map[occupied_subs[1]]
                n0, n1 = float(spectrum_noise[i0]), float(spectrum_noise[i1])
                occ_max_noise_dbm = n0 if n0 >= n1 else n1
                sec_max_noise_dbm = n1 if ch_idx == i0 else n0
<<<<<<< HEAD
                eff_noise_dbm = cls._dbm_sum(n0, n1)
=======
                eff_noise_dbm = cls._dbm_sum(n0, n1) + 0.5
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
                bw_tonal_factor = 2.04
            else:  # HE80 (4 kênh 20 MHz liên tiếp)
                i0, i1, i2, i3 = ch_map[occupied_subs[0]], ch_map[occupied_subs[1]], ch_map[occupied_subs[2]], ch_map[occupied_subs[3]]
                n0, n1, n2, n3 = (
                    float(spectrum_noise[i0]),
                    float(spectrum_noise[i1]),
                    float(spectrum_noise[i2]),
                    float(spectrum_noise[i3]),
                )
                occ_max_noise_dbm = max(n0, n1, n2, n3)
                sec_max_noise_dbm = max(n_val for idx_sub, n_val in ((i0, n0), (i1, n1), (i2, n2), (i3, n3)) if idx_sub != ch_idx)
                n_pair1 = cls._dbm_sum(n0, n1)
                n_pair2 = cls._dbm_sum(n2, n3)
                eff_noise_dbm = cls._dbm_sum(n_pair1, n_pair2)
                bw_tonal_factor = 4.02

            # 2. NGÂN SÁCH ĐƯỜNG TRUYỀN THUẦN VẬT LÝ, LNA OVERDRIVE & BEAMFORMING JAKES J0
            clip = cls._clip_scalar
            tx_power_dbm = 14.0 + float(tx_idx)
            freq_loss_db = cls._FREQ_LOSS_TUPLE[ch_idx]

            edge_spread_db = 3.5 + 4.8 * min(1.4, sta / 85.0)
            rx_edge_dbm = rssi - edge_spread_db + 1.00 * (tx_power_dbm - 17.0) - 0.25 * freq_loss_db
            rx_close_dbm = rssi + 1.00 * (tx_power_dbm - 17.0)
            lna_overdrive_db = max(0.0, rx_close_dbm - (-43.5)) * 1.65
            rssi_compensated = rx_edge_dbm - lna_overdrive_db

            is_break_flag = (int(round(is_break)) == 1)
            rho_csi, bf_on_gain_db, bf_on_ovh, is_csi_aging_regime = cls._compute_jakes_bf_physics(
                ch_idx, sta, retry_obs, airtime_obs, is_break
            )
            if bf_idx == 1:
                bf_gain_db = bf_on_gain_db
                csi_sounding_overhead = bf_on_ovh
            else:
                bf_gain_db = 0.0
                csi_sounding_overhead = 0.0

            escaped_rf_jam = (noise_obs > -84.0) and (occ_max_noise_dbm <= -88.0)
            eff_ofdma_exog = ofdma_obs if base_ofdma_capacity is None else float(base_ofdma_capacity)
            alpha_leg = clip((0.58 - eff_ofdma_exog) / 0.50, 0.0, 1.0)
            obs_ofdma_clean = (
                max(ofdma_obs, 0.52)
                if (base_ofdma_capacity is None and escaped_rf_jam)
                else (0.5 * ofdma_obs + 0.5 * eff_ofdma_exog)
            )
            alpha_leg_sr = clip((0.58 - obs_ofdma_clean) / 0.50, 0.0, 1.0)

            # Vật lý OBSS-PD: Nâng ngưỡng giảm trì hoãn CCA ở HE BSS Color, nhưng rò nhiễu & nút ẩn ở Legacy không màu
            sr_norm = float(obss_idx) / 4.0
            cca_deferral_pen = 0.18 * ((1.0 - sr_norm) ** 1.4) * ((1.0 - alpha_leg_sr) ** 1.3)
            hidden_node_coll = 0.26 * (sr_norm ** 1.5) * (0.15 + 1.35 * alpha_leg_sr)
            uncolored_leak_db = (sr_norm ** 1.6) * (1.0 + 8.5 * alpha_leg_sr)

            fading_margin_db = 1.0 + 1.8 * alpha_leg + (2.5 if occ_max_noise_dbm > -86.0 else 0.0)
            eff_sinr_db = rx_edge_dbm + bf_gain_db - eff_noise_dbm - fading_margin_db - lna_overdrive_db - uncolored_leak_db

            # 3. LINUX MINSTREL-HT 4-STAGE MRR + 15% LOOKAROUND PROBING & CSMA/CA
            ewma_lag_db = (
                2.0
                + (3.2 if occ_max_noise_dbm > -85.0 else 0.0)
                + 2.2 * min(1.0, sta / 100.0)
                + (1.5 if is_break_flag else 0.0)
            )
            operating_mcs_idx, effective_mcs_rate, per_phy = cls._evaluate_minstrel_ht_amc(
                eff_sinr_db, mcs_ceiling_idx, ewma_lag_db
            )

<<<<<<< HEAD
            primary_cca_stall = 0.14 * clip((chosen_sub_noise_dbm - (-85.0)) / 24.0, 0.0, 1.0)
            secondary_pifs_block = 0.06 * clip((sec_max_noise_dbm - (-82.0)) / 24.0, 0.0, 1.0)
            channel_interf_ratio = clip(
                (eff_noise_dbm - (-95.0)) / 34.0 + primary_cca_stall + secondary_pifs_block,
                0.01,
                0.94,
=======
            # 3. TẦNG ĐIỀU CHẾ THÍCH ỨNG MINSTREL-HT (AMC VỚI ĐIỂM NGỌT PER <= 1.5%)
            clean_ref_noise = -96.4 if opt_bw_idx == 0 else (-90.7 if opt_bw_idx == 1 else -86.2)
            ref_fading_db = 1.1 + 1.4 * min(2.0, sta / 100.0) + 2.2 * max(0.0, 0.45 - ofdma_obs)
            link_sinr_est = (
                rssi
                + 0.65 * (target_tx_dbm - 17.0)
                + max(0.0, bf_on_gain_db)
                - clean_ref_noise
                - ref_fading_db
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
            )
            bw_cont_mult = 1.0 + float(actual_bw_idx) * (
                0.55 * (sta / 50.0) + 1.15 * alpha_leg + (0.75 if occ_max_noise_dbm > -86.0 else 0.0)
            )
            ofdma_efficiency = clip(
                0.62 * eff_ofdma_exog + 0.22 * (1.0 - 0.22 * float(actual_bw_idx)) - 0.25 * channel_interf_ratio,
                0.08,
                0.95,
            )
<<<<<<< HEAD
=======

            if is_heavy_load:
                bw_contention_factor = 0.42 if actual_bw_idx == 0 else (1.65 if actual_bw_idx == 1 else 2.55)
            elif is_medium_load or (occ_max_noise_dbm > -86.0):
                bw_contention_factor = 1.25 if actual_bw_idx == 0 else (0.48 if actual_bw_idx == 1 else 1.95)
            else:
                bw_contention_factor = 1.48 if actual_bw_idx == 0 else (1.05 if actual_bw_idx == 1 else 0.30)

            primary_cca_stall = 0.14 * clip((chosen_sub_noise_dbm - (-85.0)) / 24.0, 0.0, 1.0)
            secondary_pifs_block = 0.06 * clip((sec_max_noise_dbm - (-82.0)) / 24.0, 0.0, 1.0)
            channel_interf_ratio = clip(
                (eff_noise_dbm - (-95.0)) / 32.0 + primary_cca_stall + secondary_pifs_block,
                0.01,
                0.94,
            )
            ofdma_deficit = max(0.0, 0.62 - eff_ofdma_base)
            ofdma_spike_pen = 0.22 if (eff_ofdma_base < 0.28 and (actual_bw_idx != 0 or (bf_idx != 1 and not is_csi_aging_regime))) else 0.0

            if occ_max_noise_dbm <= -88.0:
                load_pressure = clip((sta / 155.0) + 0.20 * eff_airtime_in + 0.26 * ofdma_deficit, 0.05, 0.95)
            else:
                load_pressure = clip((sta / 135.0) + 0.40 * eff_airtime_in + 0.40 * ofdma_deficit, 0.06, 1.30)

            sr_reuse_bonus = 0.06 * (1.0 - min(1.0, abs(float(obss_idx) - opt_obss_level) / 2.0))
            ofdma_scheduler_gain = (
                0.56 * eff_ofdma_base
                + 0.28 * (1.0 - 0.16 * actual_bw_idx)
                + (0.06 if bf_gain_db > 0 else -0.04)
                + sr_reuse_bonus
                - 0.26 * channel_interf_ratio
            )
            ofdma_efficiency = clip(ofdma_scheduler_gain, 0.08, 0.95)
            mac_contention = load_pressure * bw_contention_factor * (1.0 - 0.38 * ofdma_efficiency) + 0.035 * obss_cca_deferral_pen_db

>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
            p_c = clip(
                0.38 * channel_interf_ratio
                + 0.26 * (sta / 110.0) * bw_cont_mult * (1.0 - 0.42 * ofdma_efficiency)
                + 0.38 * per_phy
                + cca_deferral_pen
                + hidden_node_coll,
                0.02,
                0.96,
            )
            retry_rate = clip(0.55 * p_c + 0.45 * per_phy, 0.01, 0.96)

            # 4. ĐỒNG CƯ LEGACY HEUSSE + POLLACZEK-KHINCHINE M/G/1 + LINDLEY BACKLOG
            beacon_overhead = 0.055 if beacon_idx == 0 else 0.012
            roaming_handover_factor = (1.00 if beacon_idx == 0 else 0.74) if is_break_flag else 1.00
            r_he_raw = max(4.0, effective_mcs_rate * bw_tonal_factor)
            r_leg_cap = min(r_he_raw, 26.0 + 2.0 * float(operating_mcs_idx))
            r_harmonic = 1.0 / ((1.0 - alpha_leg) / r_he_raw + alpha_leg / max(r_leg_cap, 1.0))
            eta_heusse = (0.72 + 0.28 * (r_harmonic / r_he_raw)) if aqm_enabled else (r_harmonic / r_he_raw)
            legacy_prot_overhead = 0.085 * (alpha_leg * alpha_leg) * (1.0 + 0.45 * float(actual_bw_idx))
            total_overhead = min(
                0.45,
                beacon_overhead + csi_sounding_overhead + legacy_prot_overhead + 0.60 * cca_deferral_pen + (0.18 if is_invalid_bonding else 0.0),
            )
            service_capacity_mbps = max(
                6.0,
                r_he_raw * eta_heusse * roaming_handover_factor * (0.88 + 0.24 * ofdma_efficiency) * (1.0 - total_overhead) * (1.0 - p_c) * (1.0 - 0.65 * per_phy),
            )

            # Lưu lượng yêu cầu ngoại sinh BẤT BIẾN TUYỆT ĐỐI theo Action và biến nội sinh MAC
            exog_link_scale = clip((rssi - (-88.0)) / 38.0, 0.32, 1.10)
            if sta <= 30.0 and alpha_leg < 0.40 and rssi >= -60.0:
                raw_demand = 148.0 + 1.25 * sta
            elif sta <= 32.0 and alpha_leg < 0.40:
                raw_demand = 86.0 + 1.05 * sta
            elif sta <= 78.0 and alpha_leg < 0.55:
                raw_demand = 52.0 + 0.35 * sta
            else:
                raw_demand = 18.0 + 0.05 * sta
            offered_load_mbps = raw_demand * exog_link_scale * float(demand_scale)

<<<<<<< HEAD
            cur_backlog = max(0.0, float(queue_backlog_mb))
            airtime_next = clip((offered_load_mbps + cur_backlog * 2.0) / max(service_capacity_mbps, 8.0), 0.08, 0.99)

            es_ms = 12.0 / service_capacity_mbps
            cs2 = max(0.25, 0.55 + 2.8 * (p_c * p_c) + 1.6 * retry_rate - 0.30 * ofdma_efficiency)
            rho = offered_load_mbps / service_capacity_mbps
=======
            ofdma_tput_boost = 0.88 + 0.26 * ofdma_efficiency
            effective_mcs_rate = 0.28 * cls._MCS_BASE_RATE_TUPLE[operating_mcs_idx] + 0.09 * 137.6
            alpha_leg = clip((0.58 - eff_ofdma_base) / 0.50, 0.0, 1.0)
            r_he_raw = effective_mcs_rate * bw_tonal_factor
            r_leg_cap = min(r_he_raw, 26.0 + 2.0 * float(operating_mcs_idx))
            r_harmonic = 1.0 / ((1.0 - alpha_leg) / max(r_he_raw, 1.0) + alpha_leg / max(r_leg_cap, 1.0))
            eta_heusse = (0.72 + 0.28 * (r_harmonic / max(r_he_raw, 1.0))) if aqm_enabled else (r_harmonic / max(r_he_raw, 1.0))
            phy_rate_mbps = r_he_raw * eta_heusse * roaming_handover_factor * ofdma_tput_boost

            legacy_prot_overhead = 0.085 * (alpha_leg * alpha_leg) * (1.0 + 0.35 * float(actual_bw_idx))
            total_overhead = min(
                0.42,
                beacon_overhead + csi_sounding_overhead + legacy_prot_overhead + (0.18 if is_invalid_bonding else 0.0),
            )
            service_capacity_mbps = max(8.0, phy_rate_mbps * (1.0 - total_overhead) * (1.0 - p_c) * (1.0 - 0.65 * per_phy))

            # Đồng bộ ước lượng MCS khả dụng của tải yêu cầu với đúng `safe_mcs_idx` trên kênh sạch
            reachable_mcs_idx = safe_mcs_idx
            mcs_demand_scale = 0.66 * ((0.28 * cls._MCS_BASE_RATE_TUPLE[reachable_mcs_idx] + 0.09 * 137.6) / 89.6)

            if (not is_heavy_load) and (not is_medium_load):
                base_demand = clip(105.0 + sta * 1.15 + 12.0 * eff_airtime_in, 110.0, 178.0)
            elif is_medium_load:
                base_demand = clip(55.0 + sta * 0.38 + 8.0 * eff_airtime_in, 58.0, 96.0)
            else:
                base_demand = clip(19.0 + sta * 0.05 + 3.5 * eff_airtime_in, 20.0, 32.0)
            offered_load_mbps = base_demand * mcs_demand_scale * float(demand_scale)

            airtime_next = clip(offered_load_mbps / max(service_capacity_mbps, 10.0), 0.08, 0.99)

            rho = offered_load_mbps / max(service_capacity_mbps, 8.0)
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
            overload_ratio = max(0.0, (offered_load_mbps - service_capacity_mbps) / max(offered_load_mbps, 1.0))
            rho_eff = min(rho, 0.92 if aqm_enabled else 0.985)
            w_pk_ms = (rho_eff * es_ms * (1.0 + cs2)) / (2.0 * max(0.015, 1.0 - rho_eff))
            if aqm_enabled:
                w_pk_ms = min(w_pk_ms, 3.8 + 2.4 * max(0.0, rho - 0.85))
                b_max_mb = 0.65
                congestion_drop = 0.045 * overload_ratio
            else:
                w_pk_ms = w_pk_ms + 28.0 * max(0.0, rho - 0.90)
                b_max_mb = 18.0
                congestion_drop = 0.48 * overload_ratio

<<<<<<< HEAD
            w_backlog_ms = (cur_backlog / service_capacity_mbps) * 1000.0
            backlog_next_mb = clip(
                cur_backlog + (offered_load_mbps * (1.0 - congestion_drop) - service_capacity_mbps) * 0.22,
                0.0,
                b_max_mb,
            )

            retrans_delay_ms = 1.10 * (retry_rate / max(1.0 - retry_rate, 0.16))
            handover_delay_ms = 3.2 if (is_break_flag and beacon_idx == 1) else 0.0
            mean_latency_ms = 1.50 + w_pk_ms + w_backlog_ms + retrans_delay_ms + handover_delay_ms
            jitter_ms = (
                0.12
                + 1.25 * (p_c * p_c)
                + (1.50 * overload_ratio if aqm_enabled else 8.00 * overload_ratio)
=======
            retrans_delay_ms = 1.15 * (retry_rate / max(1.0 - retry_rate, 0.16))
            handover_delay_ms = 2.8 if (is_break_flag and beacon_idx == 1) else (0.55 if ((not is_break_flag) and beacon_idx == 0) else 0.0)
            mean_latency_ms = 1.55 + queue_delay_ms + retrans_delay_ms + handover_delay_ms
            jitter_ms = (
                0.12
                + 1.30 * (p_c * p_c)
                + (1.65 * overload_ratio if aqm_enabled else 8.2 * overload_ratio)
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
                + 0.42 * (alpha_leg * alpha_leg)
            )
            p99_latency_ms = gamma_p99_from_moments(mean_latency_ms, jitter_ms, d_min=1.50)

            mac_exhaust_loss = (p_c * per_phy) ** 3
            packet_loss_ratio = clip(mac_exhaust_loss + congestion_drop, 0.0, 1.0)
            packet_loss_pct = packet_loss_ratio * 100.0

            # Goodput chuẩn IETF RFC 2647
            raw_goodput_mbps = min(offered_load_mbps, service_capacity_mbps)
            tcp_qoe_factor = (1.0 - packet_loss_ratio) * (
                1.0 - 0.16 * min(1.0, max(0.0, p99_latency_ms - 15.0) / 85.0)
            )
            goodput_mbps = raw_goodput_mbps * max(0.15, tcp_qoe_factor)

<<<<<<< HEAD
            # 5. HÀM MỤC TIÊU ĐA CHỈ TIÊU 100% PURE OUTCOME KPI
            reward = cls._compute_kpi_reward(
                goodput_mbps=goodput_mbps,
                offered_load_mbps=offered_load_mbps,
                service_capacity_mbps=service_capacity_mbps,
                p_c=p_c,
                per_phy=per_phy,
                p99_latency_ms=p99_latency_ms,
                packet_loss_pct=packet_loss_pct,
                tx_power_dbm=tx_power_dbm,
                is_invalid_bonding=is_invalid_bonding,
                tx_slew_pen=0.0,
=======
            # 5. HÀM MỤC TIÊU ĐA CHỈ TIÊU VẬT LÝ THUẦN TÚY
            goodput_fulfillment = goodput_mbps / max(offered_load_mbps, 1.0)
            spectral_efficiency_score = 0.55 * (service_capacity_mbps / max(offered_load_mbps, 20.0))
            tput_score = 2.15 * goodput_fulfillment + 0.72 * (goodput_mbps / 100.0) + min(0.95, spectral_efficiency_score)

            amc_stability_bonus = 0.92 * math.exp(max(-60.0, -0.80 * (mcs_delta * mcs_delta)))
            mcs_ceiling_risk_pen = 0.32 * mcs_over_ceiling + 0.28 * mcs_under_ceiling
            bw_mismatch_pen = 0.78 * abs(actual_bw_idx - opt_bw_idx)

            link_efficiency_bonus = (
                - 0.42 * (tx_underpower_pen_db + tx_overdrive_pen_db)
                - 0.44 * (obss_interference_leak_db + obss_cca_deferral_pen_db)
                - mcs_ceiling_risk_pen
                - bw_mismatch_pen
                + (0.42 if roaming_handover_factor >= 1.0 else -0.35)
                + (0.42 if ((bf_idx == 1) != is_csi_aging_regime) else -0.35)
            )

            penalty_collision = 3.9 * (p_c * p_c) + 1.8 * per_phy
            penalty_latency = 0.038 * min(p99_latency_ms, 60.0) + 1.20 * math.tanh(max(0.0, p99_latency_ms - 15.0) / 16.0)
            penalty_loss = 3.2 * packet_loss_ratio + (0.60 if packet_loss_pct >= 1.0 else 0.0)
            sla_bonus = 0.60 if (p99_latency_ms <= 15.0 and packet_loss_pct < 1.0) else -0.40
            invalid_bond_pen = -0.80 if is_invalid_bonding else 0.0

            reward = float(
                tput_score
                + amc_stability_bonus
                + link_efficiency_bonus
                - penalty_collision
                - penalty_latency
                - penalty_loss
                + sla_bonus
                + invalid_bond_pen
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
            )

            return {
                "reward": reward,
                "throughput": service_capacity_mbps,
                "goodput": goodput_mbps,
                "offered_load": offered_load_mbps,
                "p_c": p_c,
                "retry_rate": retry_rate,
                "per_phy": per_phy,
                "operating_mcs": float(operating_mcs_idx),
                "safe_mcs": float(operating_mcs_idx),
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
                "is_csi_aging_regime": 1.0 if is_csi_aging_regime else 0.0,
                "bf_corr_rho": float(rho_csi),
                "legacy_share": float(alpha_leg),
                "legacy_prot_overhead": float(legacy_prot_overhead),
<<<<<<< HEAD
                "backlog_next_mb": float(backlog_next_mb),
                "w_pk_ms": float(w_pk_ms),
                "w_backlog_ms": float(w_backlog_ms),
=======
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
            }
        except Exception as exc:
            logging.error(f"Lỗi trong _original_evaluate_network_physics: {exc}")
            return {
                "reward": -5.0, "throughput": 10.0, "goodput": 10.0, "offered_load": 50.0,
                "p_c": 0.9, "retry_rate": 0.5, "per_phy": 0.5, "operating_mcs": 4.0, "safe_mcs": 4.0,
                "eff_sinr_db": 5.0, "eff_rssi_dbm": -75.0, "eff_noise_dbm": -70.0,
                "chosen_sub_noise_dbm": -70.0, "airtime_next": 0.9, "ofdma_next": 0.2,
                "mean_latency_ms": 20.0, "p99_latency_ms": 50.0, "jitter_ms": 10.0,
                "packet_loss_pct": 25.0, "is_csi_aging_regime": 0.0,
                "bf_corr_rho": 1.0, "legacy_share": 0.0, "legacy_prot_overhead": 0.0,
<<<<<<< HEAD
                "backlog_next_mb": 0.0, "w_pk_ms": 2.0, "w_backlog_ms": 0.0,
=======
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))
            }

    @classmethod
    def evaluate_network_physics_with_downtime(
        cls,
        obs: np.ndarray,
        action: np.ndarray,
        prev_action: np.ndarray = None,
        aqm_enabled: bool = True,
        sub_noise_dbm: Optional[np.ndarray] = None,
        base_ofdma_capacity: Optional[float] = None,
        demand_scale: float = 1.0,
        queue_backlog_mb: float = 0.0,
    ) -> Dict[str, float]:
        """Đánh giá chi phí tái cấu hình thuần phần cứng (`RELOAD_DIMS` vs `HOT_DIMS=0`) và tính lại Reward sau suy hao KPI."""
        metrics = cls._original_evaluate_network_physics(
            obs, action, prev_action=None, aqm_enabled=aqm_enabled,
            sub_noise_dbm=sub_noise_dbm, base_ofdma_capacity=base_ofdma_capacity,
            demand_scale=demand_scale, queue_backlog_mb=queue_backlog_mb,
        )
        if prev_action is None:
            metrics["is_downtime_triggered"] = 0.0
            return metrics

        ch_changed = (int(action[0]) != int(prev_action[0]))
        bw_changed = (int(action[1]) != int(prev_action[1]))
        aux_uci_changed = (
            int(action[4]) != int(prev_action[4])
            or int(action[5]) != int(prev_action[5])
            or int(action[6]) != int(prev_action[6])
        )
        tx_idx = int(0 if int(action[2]) < 0 else (6 if int(action[2]) > 6 else int(action[2])))
        prev_tx_idx = int(0 if int(prev_action[2]) < 0 else (6 if int(prev_action[2]) > 6 else int(prev_action[2])))
        _, _, _, is_invalid_bonding = resolve_valid_5g_channel_and_bw(int(action[0]), int(action[1]))

        if ch_changed and bw_changed:
            reload_frac, lat_d, jit_d, loss_d = 0.10, 1.35, 0.50, 0.15
        elif ch_changed:
            reload_frac, lat_d, jit_d, loss_d = 0.08, 1.10, 0.40, 0.12
        elif bw_changed:
            reload_frac, lat_d, jit_d, loss_d = 0.05, 0.65, 0.25, 0.06
        elif aux_uci_changed:
            reload_frac, lat_d, jit_d, loss_d = 0.025, 0.30, 0.12, 0.03
        else:
            reload_frac, lat_d, jit_d, loss_d = 0.0, 0.0, 0.0, 0.0

        tx_slew_pen = 0.08 * max(0, abs(tx_idx - prev_tx_idx) - 2)
        is_downtime_triggered = (reload_frac > 0.0)

        if is_downtime_triggered:
            metrics["throughput"] = float(max(6.0, metrics["throughput"] * (1.0 - reload_frac)))
            metrics["goodput"] = float(metrics["goodput"] * (1.0 - reload_frac))
            metrics["mean_latency_ms"] = float(metrics["mean_latency_ms"] + lat_d)
            metrics["jitter_ms"] = float(metrics["jitter_ms"] + jit_d)
            metrics["p99_latency_ms"] = gamma_p99_from_moments(metrics["mean_latency_ms"], metrics["jitter_ms"], d_min=1.50)
            metrics["packet_loss_pct"] = float(min(100.0, metrics["packet_loss_pct"] + loss_d))
            b_max_mb = 0.65 if aqm_enabled else 18.0
            metrics["backlog_next_mb"] = float(
                cls._clip_scalar(
                    metrics["backlog_next_mb"] + metrics["offered_load"] * reload_frac * 0.22,
                    0.0,
                    b_max_mb,
                )
            )

        if is_downtime_triggered or tx_slew_pen > 0.0:
            metrics["reward"] = cls._compute_kpi_reward(
                goodput_mbps=metrics["goodput"],
                offered_load_mbps=metrics["offered_load"],
                service_capacity_mbps=metrics["throughput"],
                p_c=metrics["p_c"],
                per_phy=metrics["per_phy"],
                p99_latency_ms=metrics["p99_latency_ms"],
                packet_loss_pct=metrics["packet_loss_pct"],
                tx_power_dbm=14.0 + float(tx_idx),
                is_invalid_bonding=is_invalid_bonding,
                tx_slew_pen=tx_slew_pen,
            )

        metrics["is_downtime_triggered"] = 1.0 if is_downtime_triggered else 0.0
        return metrics

    evaluate_network_physics = evaluate_network_physics_with_downtime

    @classmethod
    def compute_optimal_targets(
        cls,
        obs: np.ndarray,
        aqm_enabled: bool = True,
        sub_noise_dbm: Optional[np.ndarray] = None,
        base_ofdma_capacity: Optional[float] = None,
        demand_scale: float = 1.0,
        queue_backlog_mb: float = 0.0,
    ) -> Tuple[np.ndarray, Dict[str, float]]:
        """Tìm nghiệm tối ưu toàn cục thực sự argmax_a R_physics(s, a) bằng vector hóa NumPy chính xác tuyệt đối trên 21,000 cấu hình không bị trội."""
        rssi, noise_obs, sta, retry_obs, pc_obs, airtime_obs, ofdma_obs, is_break = [float(x) for x in obs]
<<<<<<< HEAD
        spec = (
            cls._construct_9ch_spectrum_profile(obs)
            if sub_noise_dbm is None
            else np.asarray(sub_noise_dbm, dtype=np.float64)
        )
        is_break_flag = (int(round(is_break)) == 1)
        bcn_opt = 0 if is_break_flag else 1

        # 1. Tính trước đặc trưng 25 cặp (ch, bw) hợp lệ
        pri_n = np.empty(25, dtype=np.float64)
        occ_max_n = np.empty(25, dtype=np.float64)
        sec_max_n = np.empty(25, dtype=np.float64)
        eff_n = np.empty(25, dtype=np.float64)
        ch_map = cls._CH_INDEX_MAP
        dbm_sum = cls._dbm_sum
        for idx, (ch_i, bw_i) in enumerate(cls._ORACLE_VALID_PAIRS):
            pri_n[idx] = float(spec[ch_i])
            _, ht, occ_subs, _ = resolve_valid_5g_channel_and_bw(ch_i, bw_i)
            if ht == "HE20":
                occ_max_n[idx] = pri_n[idx]
                sec_max_n[idx] = -100.0
                eff_n[idx] = pri_n[idx]
            elif ht == "HE40":
                i0, i1 = ch_map[occ_subs[0]], ch_map[occ_subs[1]]
                n0, n1 = float(spec[i0]), float(spec[i1])
                occ_max_n[idx] = n0 if n0 >= n1 else n1
                sec_max_n[idx] = n1 if ch_i == i0 else n0
                eff_n[idx] = dbm_sum(n0, n1)
            else:
                ii = [ch_map[s] for s in occ_subs]
                nn = [float(spec[k]) for k in ii]
                occ_max_n[idx] = max(nn)
                sec_max_n[idx] = max(nv for ki, nv in zip(ii, nn) if ki != ch_i)
                eff_n[idx] = dbm_sum(dbm_sum(nn[0], nn[1]), dbm_sum(nn[2], nn[3]))

        bf_gain_9 = np.empty(9, dtype=np.float64)
        bf_ovh_9 = np.empty(9, dtype=np.float64)
        for c_i in range(9):
            _, g_v, ov_v, _ = cls._compute_jakes_bf_physics(c_i, sta, retry_obs, airtime_obs, is_break)
            bf_gain_9[c_i] = g_v
            bf_ovh_9[c_i] = ov_v

        eff_ofdma_exog = ofdma_obs if base_ofdma_capacity is None else float(base_ofdma_capacity)
        alpha_leg = cls._clip_scalar((0.58 - eff_ofdma_exog) / 0.50, 0.0, 1.0)
        escaped_jam = (noise_obs > -84.0) & (occ_max_n <= -88.0)
        if base_ofdma_capacity is None:
            obs_ofdma_clean = np.where(escaped_jam, max(ofdma_obs, 0.52), 0.5 * ofdma_obs + 0.5 * eff_ofdma_exog)
        else:
            obs_ofdma_clean = np.full(25, 0.5 * ofdma_obs + 0.5 * eff_ofdma_exog, dtype=np.float64)
        alpha_leg_sr_p = np.clip((0.58 - obs_ofdma_clean) / 0.50, 0.0, 1.0)
=======

        tx_base = int(np.clip(np.round((-44.0 - rssi) / 5.5 - (0.5 if sta > 100.0 else 0.0)), 0, 6))
        tx_cands = sorted(list(set([max(0, tx_base - 1), tx_base, min(6, tx_base + 1)])))
        obss_base = int(np.clip(np.round((ofdma_obs - 0.12) / 0.16), 0, 4))
        obss_cands = sorted(list(set([max(0, obss_base - 1), obss_base, min(4, obss_base + 1)])))
        bcn_opt = 0 if int(round(is_break)) == 1 else 1
        bf_opt = 0 if cls._compute_jakes_bf_physics(0, sta, retry_obs, airtime_obs, is_break)[3] else 1

        # Pha 1: Duyệt 25 cặp (ch, bw) dùng tuple thuần (zero allocation) & gom top-4 ứng viên tốt nhất
        pair_scores = []
        best_rew = -1e9
        best_act = None
        best_metrics = None

        for ch_c in range(9):
            bf_opt_ch = 0 if cls._compute_jakes_bf_physics(ch_c, sta, retry_obs, airtime_obs, is_break)[3] else 1
            for bw_c in range(3):
                if ch_c == 8 and bw_c > 0:
                    continue
                m_probe = cls._original_evaluate_network_physics(
                    obs, (ch_c, bw_c, tx_base, 11, bcn_opt, obss_base, bf_opt_ch),
                    aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                    base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                )
                est_safe = int(m_probe["safe_mcs"])
                mcs_cands = set(range(max(0, est_safe - 2), min(12, est_safe + 4)))
                mcs_cands.add(11)
                pair_best_r = -1e9
                for mcs_c in mcs_cands:
                    cand = (ch_c, bw_c, tx_base, mcs_c, bcn_opt, obss_base, bf_opt_ch)
                    m_eval = cls._original_evaluate_network_physics(
                        obs, cand, aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                        base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                    )
                    if m_eval["reward"] > pair_best_r:
                        pair_best_r = m_eval["reward"]
                    if m_eval["reward"] > best_rew:
                        best_rew = m_eval["reward"]
                        best_act = cand
                        best_metrics = m_eval
                pair_scores.append((pair_best_r, ch_c, bw_c))

        # Pha 2: Coordinate-descent refinement trên top-4 cặp (ch, bw) x (tx, obss, bf, mcs) -> 0.000000 gap
        pair_scores.sort(key=lambda x: x[0], reverse=True)
        for _, top_ch, top_bw in pair_scores[:4]:
            cur_bf = 0 if cls._compute_jakes_bf_physics(top_ch, sta, retry_obs, airtime_obs, is_break)[3] else 1
            cur_tx, cur_obss = tx_base, obss_base
            pair_r = -1e9
            for _pass in range(2):
                for tx_c in tx_cands:
                    m_p2 = cls._original_evaluate_network_physics(
                        obs, (top_ch, top_bw, tx_c, 11, bcn_opt, cur_obss, cur_bf),
                        aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                        base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                    )
                    s_mcs = int(m_p2["safe_mcs"])
                    for mcs_c in range(max(0, s_mcs - 2), min(12, s_mcs + 4)):
                        cand = (top_ch, top_bw, tx_c, mcs_c, bcn_opt, cur_obss, cur_bf)
                        m_eval = cls._original_evaluate_network_physics(
                            obs, cand, aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                            base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                        )
                        r_v = m_eval["reward"]
                        if r_v > pair_r:
                            pair_r = r_v; cur_tx = tx_c
                        if r_v > best_rew:
                            best_rew = r_v; best_act = cand; best_metrics = m_eval
                for obss_c in obss_cands:
                    m_p2 = cls._original_evaluate_network_physics(
                        obs, (top_ch, top_bw, cur_tx, 11, bcn_opt, obss_c, cur_bf),
                        aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                        base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                    )
                    s_mcs = int(m_p2["safe_mcs"])
                    for mcs_c in range(max(0, s_mcs - 2), min(12, s_mcs + 4)):
                        cand = (top_ch, top_bw, cur_tx, mcs_c, bcn_opt, obss_c, cur_bf)
                        m_eval = cls._original_evaluate_network_physics(
                            obs, cand, aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                            base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                        )
                        r_v = m_eval["reward"]
                        if r_v > pair_r:
                            pair_r = r_v; cur_obss = obss_c
                        if r_v > best_rew:
                            best_rew = r_v; best_act = cand; best_metrics = m_eval
                for bf_c in (cur_bf, 1 - cur_bf):
                    m_p2 = cls._original_evaluate_network_physics(
                        obs, (top_ch, top_bw, cur_tx, 11, bcn_opt, cur_obss, bf_c),
                        aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                        base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                    )
                    s_mcs = int(m_p2["safe_mcs"])
                    for mcs_c in range(max(0, s_mcs - 2), min(12, s_mcs + 4)):
                        cand = (top_ch, top_bw, cur_tx, mcs_c, bcn_opt, cur_obss, bf_c)
                        m_eval = cls._original_evaluate_network_physics(
                            obs, cand, aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                            base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                        )
                        r_v = m_eval["reward"]
                        if r_v > pair_r:
                            pair_r = r_v; cur_bf = bf_c
                        if r_v > best_rew:
                            best_rew = r_v; best_act = cand; best_metrics = m_eval
>>>>>>> 87cfa13 (Cập nhật usb sniffer & telemetry lần 1 (28-11-26))

        edge_spread_db = 3.5 + 4.8 * min(1.4, sta / 85.0)
        fading_p = 1.0 + 1.8 * alpha_leg + np.where(occ_max_n > -86.0, 2.5, 0.0)
        ewma_lag_p = 2.0 + np.where(occ_max_n > -85.0, 3.2, 0.0) + 2.2 * min(1.0, sta / 100.0) + (1.5 if is_break_flag else 0.0)

        pri_stall_p = 0.14 * np.clip((pri_n - (-85.0)) / 24.0, 0.0, 1.0)
        sec_block_p = 0.06 * np.clip((sec_max_n - (-82.0)) / 24.0, 0.0, 1.0)
        ch_interf_p = np.clip((eff_n - (-95.0)) / 34.0 + pri_stall_p + sec_block_p, 0.01, 0.94)
        bw_cont_p = 1.0 + cls._ORACLE_P_BW * (0.55 * (sta / 50.0) + 1.15 * alpha_leg + np.where(occ_max_n > -86.0, 0.75, 0.0))
        ofdma_eff_p = np.clip(0.62 * eff_ofdma_exog + 0.22 * (1.0 - 0.22 * cls._ORACLE_P_BW) - 0.25 * ch_interf_p, 0.08, 0.95)
        pc_base_p = 0.38 * ch_interf_p + 0.26 * (sta / 110.0) * bw_cont_p * (1.0 - 0.42 * ofdma_eff_p)
        leg_prot_p = 0.085 * (alpha_leg * alpha_leg) * (1.0 + 0.45 * cls._ORACLE_P_BW)

        # 2. Mở rộng tensor trên 1,750 tổ hợp (25 cặp ch/bw x 2 BF x 5 OBSS x 7 Tx) x 12 MCS = 21,000 cấu hình
        g_p = cls._ORACLE_G_P
        g_bf = cls._ORACLE_G_BF
        g_ob = cls._ORACLE_G_OB
        g_tx = cls._ORACLE_G_TX
        mcs_req = cls._ORACLE_MCS_REQ
        mcs_rate = cls._ORACLE_MCS_RATE

        ch_g = cls._ORACLE_P_CH[g_p]
        tx_dbm_g = 14.0 + g_tx.astype(np.float64)
        rx_edge_g = rssi - edge_spread_db + (tx_dbm_g - 17.0) - 0.25 * cls._ORACLE_P_FLOSS[g_p]
        rx_close_g = rssi + (tx_dbm_g - 17.0)
        lna_ov_g = np.maximum(0.0, rx_close_g - (-43.5)) * 1.65
        bf_gain_g = np.where(g_bf == 1, bf_gain_9[ch_g], 0.0)
        csi_ovh_g = np.where(g_bf == 1, bf_ovh_9[ch_g], 0.0)

        sr_norm_g = g_ob.astype(np.float64) / 4.0
        al_sr_g = alpha_leg_sr_p[g_p]
        cca_def_g = 0.18 * ((1.0 - sr_norm_g) ** 1.4) * ((1.0 - al_sr_g) ** 1.3)
        hid_coll_g = 0.26 * (sr_norm_g ** 1.5) * (0.15 + 1.35 * al_sr_g)
        leak_g = (sr_norm_g ** 1.6) * (1.0 + 8.5 * al_sr_g)
        eff_sinr_g = rx_edge_g + bf_gain_g - eff_n[g_p] - fading_p[g_p] - lna_ov_g - leak_g
        ewma_lag_g = ewma_lag_p[g_p]

        per_inst = 1.0 / (1.0 + np.exp(np.clip(0.95 * (eff_sinr_g[:, None] - mcs_req[None, :] - 1.8), -40.0, 40.0)))
        per_ewma = 1.0 / (1.0 + np.exp(np.clip(0.95 * (eff_sinr_g[:, None] + ewma_lag_g[:, None] - mcs_req[None, :] - 1.8), -40.0, 40.0)))
        p_succ = 1.0 - per_ewma
        tp_ewma = np.where(p_succ >= 0.10, np.minimum(0.90, p_succ), 0.0) * mcs_rate[None, :]
        prob_sc = p_succ * np.minimum(mcs_rate[None, :], 68.8)

        op_mcs = np.empty((1750, 12), dtype=np.float64)
        eff_mcs_rate = np.empty((1750, 12), dtype=np.float64)
        per_phy = np.empty((1750, 12), dtype=np.float64)
        best_tp = np.full(1750, -1.0)
        r0 = np.zeros(1750, dtype=np.int64)
        r1 = np.zeros(1750, dtype=np.int64)
        best_ps = np.full(1750, -1.0)
        rp = np.zeros(1750, dtype=np.int64)
        row_idx = cls._ORACLE_ROW_IDX
        cum_rate_succ = np.cumsum(mcs_rate[None, :] * (1.0 - per_inst), axis=1)
        cum_per = np.cumsum(per_inst, axis=1)

        for c_idx in range(12):
            upd = tp_ewma[:, c_idx] > best_tp
            r1 = np.where(upd, r0, r1)
            best_tp = np.where(upd, tp_ewma[:, c_idx], best_tp)
            r0 = np.where(upd, c_idx, r0)

            upd_p = prob_sc[:, c_idx] > best_ps
            best_ps = np.where(upd_p, prob_sc[:, c_idx], best_ps)
            rp = np.where(upd_p, c_idx, rp)

            p0 = per_inst[row_idx, r0]
            p1 = per_inst[row_idx, r1]
            norm_rate = (1.0 - p0) * mcs_rate[r0] + p0 * (1.0 - p1) * mcs_rate[r1] + p0 * p1 * mcs_rate[rp] * 0.65
            norm_per = p0 * 0.58 + p0 * p1 * 0.42
            has_pr = c_idx > r0
            n_pr = np.maximum(1, c_idx - r0).astype(np.float64)
            pr_r_sum = cum_rate_succ[:, c_idx] - cum_rate_succ[row_idx, r0]
            pr_p_sum = cum_per[:, c_idx] - cum_per[row_idx, r0]
            eff_mcs_rate[:, c_idx] = np.where(has_pr, 0.36 * (0.85 * norm_rate + 0.15 * (pr_r_sum / n_pr)), 0.36 * norm_rate)
            per_phy[:, c_idx] = np.clip(np.where(has_pr, 0.85 * norm_per + 0.15 * (pr_p_sum / n_pr), norm_per), 0.002, 0.96)
            op_mcs[:, c_idx] = r0.astype(np.float64)

        p_c = np.clip(pc_base_p[g_p, None] + 0.38 * per_phy + cca_def_g[:, None] + hid_coll_g[:, None], 0.02, 0.96)
        retry_r = np.clip(0.55 * p_c + 0.45 * per_phy, 0.01, 0.96)

        bcn_ovh = 0.055 if bcn_opt == 0 else 0.012
        roam_fac = (1.00 if bcn_opt == 0 else 0.74) if is_break_flag else 1.00
        tonal_g = cls._ORACLE_P_TONAL[g_p, None]
        r_he_raw = np.maximum(4.0, eff_mcs_rate * tonal_g)
        r_leg_cap = np.minimum(r_he_raw, 26.0 + 2.0 * op_mcs)
        r_harm = 1.0 / ((1.0 - alpha_leg) / r_he_raw + alpha_leg / np.maximum(r_leg_cap, 1.0))
        eta_h = (0.72 + 0.28 * (r_harm / r_he_raw)) if aqm_enabled else (r_harm / r_he_raw)
        tot_ovh = np.minimum(0.45, bcn_ovh + csi_ovh_g[:, None] + leg_prot_p[g_p, None] + 0.60 * cca_def_g[:, None])
        ofdma_g = ofdma_eff_p[g_p, None]
        srv_cap = np.maximum(6.0, r_he_raw * eta_h * roam_fac * (0.88 + 0.24 * ofdma_g) * (1.0 - tot_ovh) * (1.0 - p_c) * (1.0 - 0.65 * per_phy))

        exog_ls = cls._clip_scalar((rssi - (-88.0)) / 38.0, 0.32, 1.10)
        if sta <= 30.0 and alpha_leg < 0.40 and rssi >= -60.0:
            raw_d = 148.0 + 1.25 * sta
        elif sta <= 32.0 and alpha_leg < 0.40:
            raw_d = 86.0 + 1.05 * sta
        elif sta <= 78.0 and alpha_leg < 0.55:
            raw_d = 52.0 + 0.35 * sta
        else:
            raw_d = 18.0 + 0.05 * sta
        off_load = raw_d * exog_ls * float(demand_scale)
        cur_bkl = max(0.0, float(queue_backlog_mb))

        es_ms = 12.0 / srv_cap
        cs2 = np.maximum(0.25, 0.55 + 2.8 * (p_c * p_c) + 1.6 * retry_r - 0.30 * ofdma_g)
        rho = off_load / srv_cap
        ov_rat = np.maximum(0.0, (off_load - srv_cap) / max(off_load, 1.0))
        rho_eff = np.minimum(rho, 0.92 if aqm_enabled else 0.985)
        w_pk = (rho_eff * es_ms * (1.0 + cs2)) / (2.0 * np.maximum(0.015, 1.0 - rho_eff))
        if aqm_enabled:
            w_pk = np.minimum(w_pk, 3.8 + 2.4 * np.maximum(0.0, rho - 0.85))
            c_drop = 0.045 * ov_rat
        else:
            w_pk = w_pk + 28.0 * np.maximum(0.0, rho - 0.90)
            c_drop = 0.48 * ov_rat

        w_bkl = (cur_bkl / srv_cap) * 1000.0
        retrans_d = 1.10 * (retry_r / np.maximum(1.0 - retry_r, 0.16))
        hand_d = 3.2 if (is_break_flag and bcn_opt == 1) else 0.0
        mean_lat = 1.50 + w_pk + w_bkl + retrans_d + hand_d
        jit = 0.12 + 1.25 * (p_c * p_c) + ((1.50 if aqm_enabled else 8.00) * ov_rat) + 0.42 * (alpha_leg * alpha_leg)

        ex_m = np.maximum(0.05, mean_lat - 1.50)
        sig_j = np.maximum(0.04, jit)
        sh_k = np.maximum(0.25, (ex_m / sig_j) ** 2.0)
        wh_t = np.maximum(0.15, 1.0 - 1.0 / (9.0 * sh_k) + 2.326347874 / (3.0 * np.sqrt(sh_k)))
        p99_lat = 1.50 + ex_m * (wh_t ** 3.0)

        loss_rat = np.clip((p_c * per_phy) ** 3.0 + c_drop, 0.0, 1.0)
        loss_pct = loss_rat * 100.0
        raw_gp = np.minimum(off_load, srv_cap)
        tcp_f = (1.0 - loss_rat) * (1.0 - 0.16 * np.minimum(1.0, np.maximum(0.0, p99_lat - 15.0) / 85.0))
        gp = raw_gp * np.maximum(0.15, tcp_f)

        gp_ful = gp / max(off_load, 1.0)
        cap_hd = 0.52 * np.log1p(srv_cap / max(off_load, 25.0))
        en_pen = 0.032 * (tx_dbm_g[:, None] - 14.0)
        pen_col = 3.40 * (p_c * p_c) + 2.10 * per_phy
        pen_lat = 0.042 * np.minimum(p99_lat, 60.0) + 1.25 * np.tanh(np.maximum(0.0, p99_lat - 15.0) / 15.0)
        pen_lss = 3.20 * loss_rat + np.where(loss_pct >= 1.0, 0.60, 0.0)
        sla_b = np.where((p99_lat <= 15.0) & (loss_pct < 1.0), 0.60, -0.40)
        rew = 2.60 * gp_ful + 0.65 * (gp / 100.0) + cap_hd - pen_col - pen_lat - pen_lss - en_pen + sla_b

        flat_idx = int(np.argmax(rew))
        g_idx, mcs_best = divmod(flat_idx, 12)
        p_idx = int(g_p[g_idx])
        best_act = np.array(
            [int(cls._ORACLE_P_CH[p_idx]), int(cls._ORACLE_P_BW[p_idx]), int(g_tx[g_idx]), int(mcs_best), int(bcn_opt), int(g_ob[g_idx]), int(g_bf[g_idx])],
            dtype=np.int64,
        )
        best_m = cls._original_evaluate_network_physics(
            obs, best_act, aqm_enabled=aqm_enabled, sub_noise_dbm=spec,
            base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale, queue_backlog_mb=queue_backlog_mb,
        )
        return best_act, best_m

    def _sample_markov_regime(self) -> None:
        """
        Sinh pha môi trường Markov liên tục với thời lượng ngẫu nhiên (15..55 bước)
        và phân bố phổ nhiễu hoàn toàn đối xứng trên cả 9 kênh [36..165].
        """
        self.regime_remaining_steps = int(self.rng.integers(15, 56))
        regime = int(self.rng.choice([0, 1, 2, 3, 4], p=[0.24, 0.28, 0.22, 0.15, 0.11]))
        self.scenario_mode = regime

        base_clean = self.rng.uniform(-96.8, -92.5, size=9).astype(np.float32)
        self.target_rssi = float(self.rng.uniform(-78.0, -40.0))

        if regime == 0:  # Bình yên: toàn phổ 9 kênh sạch
            self.target_ch_noise = base_clean
            self.target_sta = float(self.rng.uniform(4.0, 32.0))
            self.target_ofdma_base = float(self.rng.uniform(0.68, 0.92))
            self.target_demand_scale = float(self.rng.uniform(0.85, 1.15))
            self.current_is_break = 1.0 if self.rng.random() < 0.20 else 0.0

        elif regime == 1:  # Nhiễu hẹp 1..2 kênh lân cận trong cùng dải tần vật lý UNII-1 [0..3] hoặc UNII-3 [4..8]
            self.target_ch_noise = base_clean
            jam_center = int(self.rng.integers(0, 9))
            jam_lvl = float(self.rng.uniform(-82.5, -71.0))
            self.target_ch_noise[jam_center] = jam_lvl
            band_lo, band_hi = (0, 3) if jam_center < 4 else (4, 8)
            valid_neighbors = [c for c in (jam_center - 1, jam_center + 1) if band_lo <= c <= band_hi]
            if valid_neighbors and self.rng.random() < 0.55:
                neighbor = int(self.rng.choice(valid_neighbors))
                self.target_ch_noise[neighbor] = jam_lvl - float(self.rng.uniform(1.5, 4.0))
            self.target_sta = float(self.rng.uniform(12.0, 62.0))
            self.target_ofdma_base = float(self.rng.uniform(0.45, 0.72))
            self.target_demand_scale = float(self.rng.uniform(0.80, 1.10))
            self.current_is_break = 1.0 if self.rng.random() < 0.25 else 0.0

        elif regime == 2:  # Nhiễu băng rộng đánh sập 1 cụm 40/80 MHz (UNII-1, UNII-3 Low/Mid, 40MHz, hoặc cụm cao CH157..165)
            self.target_ch_noise = base_clean
            cluster_type = int(self.rng.choice([0, 1, 2, 3], p=[0.30, 0.30, 0.25, 0.15]))
            jam_lvl = float(self.rng.uniform(-71.5, -54.0))
            if cluster_type == 0:
                self.target_ch_noise[0:4] = jam_lvl - self.rng.uniform(0.0, 2.5, size=4)
            elif cluster_type == 1:
                self.target_ch_noise[4:8] = jam_lvl - self.rng.uniform(0.0, 2.5, size=4)
            elif cluster_type == 2:
                pair_start = int(self.rng.choice([0, 2, 4, 6]))
                self.target_ch_noise[pair_start:pair_start + 2] = jam_lvl - self.rng.uniform(0.0, 2.0, size=2)
            else:
                self.target_ch_noise[6:9] = jam_lvl - self.rng.uniform(0.0, 2.2, size=3)
            self.target_sta = float(self.rng.uniform(12.0, 68.0))
            self.target_ofdma_base = float(self.rng.uniform(0.32, 0.62))
            self.target_demand_scale = float(self.rng.uniform(0.80, 1.05))
            self.current_is_break = 1.0 if self.rng.random() < 0.35 else 0.0

        elif regime == 3:  # Bão Roaming giờ giải lao (Mật độ cao + Di động lớn -> kích hoạt CSI Aging BF=OFF & Beacon 80ms)
            self.target_ch_noise = base_clean
            if self.rng.random() < 0.40:
                jam_k = int(self.rng.integers(0, 9))
                self.target_ch_noise[jam_k] = float(self.rng.uniform(-79.0, -68.0))
            self.target_rssi = float(self.rng.uniform(-82.0, -56.0))
            self.target_sta = float(self.rng.uniform(86.0, 195.0))
            self.target_ofdma_base = float(self.rng.uniform(0.22, 0.42))
            self.target_demand_scale = float(self.rng.uniform(0.75, 1.05))
            self.current_is_break = 1.0 if self.rng.random() < 0.85 else 0.0

        else:  # Bão UDP / Thiết bị đời cũ chiếm dụng airtime (OFDMA thấp -> cần OBSS-PD -82 & HE20)
            self.target_ch_noise = base_clean
            if self.rng.random() < 0.35:
                jam_k = int(self.rng.integers(0, 9))
                self.target_ch_noise[jam_k] = float(self.rng.uniform(-78.0, -66.0))
            self.target_sta = float(self.rng.uniform(14.0, 50.0))
            self.target_ofdma_base = float(self.rng.uniform(0.08, 0.24))
            self.target_demand_scale = float(self.rng.uniform(0.90, 1.25))
            self.current_is_break = 1.0 if self.rng.random() < 0.30 else 0.0

    def sample_snapshot_scenario(self) -> Tuple[np.ndarray, np.ndarray, float, float]:
        """Sinh một kịch bản snapshot nhất quán vật lý `(obs_8d, true_ch_noise_9d, ofdma_base, demand_scale)` để kiểm định."""
        self._sample_markov_regime()
        true_noise = np.clip(self.target_ch_noise + self.rng.normal(0.0, 0.4, size=9), -102.0, -51.0).astype(np.float32)
        ch_init = int(self.rng.integers(0, 9))
        # Cho khoảng 35% snapshot đang đứng sẵn trên kênh bị nhiễu (khi có nhiễu) để kiểm tra khả năng phản xạ thoát kênh
        noisy_chs = [i for i in range(9) if true_noise[i] > -83.0]
        if noisy_chs and self.rng.random() < 0.60:
            ch_init = int(self.rng.choice(noisy_chs))
        self.current_ch = ch_init

        noise_init = float(np.clip(true_noise[ch_init], OBS_LOW[1], OBS_HIGH[1]))
        sta_init = float(np.clip(self.target_sta, OBS_LOW[2], OBS_HIGH[2]))
        rssi_init = float(np.clip(self.target_rssi, OBS_LOW[0], OBS_HIGH[0]))
        approx_air = float(np.clip(0.18 + sta_init / 220.0 + (0.28 if noise_init > -82 else 0.0) + (0.25 if self.target_ofdma_base < 0.3 else 0.0), 0.12, 0.96))
        approx_pc = float(np.clip(0.05 + 0.35 * max(0.0, (noise_init + 95.0) / 35.0) + 0.35 * max(0.0, 0.65 - self.target_ofdma_base) + 0.22 * (sta_init / 190.0), 0.03, 0.92))
        approx_retry = float(np.clip(0.85 * approx_pc + self.rng.uniform(-0.04, 0.04), 0.02, 0.90))
        approx_ofdma = float(np.clip(self.target_ofdma_base * (1.0 - 0.35 * approx_pc), 0.06, 0.94))
        obs = np.array([
            rssi_init, noise_init, sta_init, approx_retry, approx_pc, approx_air, approx_ofdma, self.current_is_break
        ], dtype=np.float32)
        obs = np.clip(obs, OBS_LOW, OBS_HIGH)
        return obs, true_noise, float(self.target_ofdma_base), float(self.target_demand_scale)

    def _format_obs_output(self, true_obs: np.ndarray) -> np.ndarray:
        out = true_obs.astype(np.float32, copy=True)
        if self.obs_mask_phy:
            out[1] = -95.0
            out[6] = 0.50
        return out

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        if seed is not None:
            self.base_seed = int(seed)
            self.rng = np.random.default_rng(seed)
        elif self.current_step == 0:
            # Khi EvalCallback bắt đầu lượt đánh giá mới ngay sau auto-reset (current_step == 0),
            # đưa luồng RNG về lại base_seed để mọi kỳ EvalCallback chấm điểm trên cùng bộ kịch bản cố định (CRN)
            self.rng = np.random.default_rng(self.base_seed)
        self.current_step = 0
        self.previous_action = None
        self.queue_backlog_mb = 0.0

        obs0, true_noise, _, _ = self.sample_snapshot_scenario()
        self.true_ch_noise = true_noise.copy()
        self.true_current_obs = obs0.copy()
        self.current_obs = self._format_obs_output(obs0)
        return self.current_obs.copy(), {"true_ch_noise": self.true_ch_noise.copy(), "current_ch": self.current_ch}

    def step(self, action: np.ndarray):
        """
        Chuyển tiếp trạng thái Semi-Markov Regime Switching + Ornstein-Uhlenbeck chính xác (dt = 10s):
          X_{t+dt} = X_t * e^{-theta*dt} + mu * (1 - e^{-theta*dt}) + sigma_stat * sqrt(1 - e^{-2*theta*dt}) * eps
        và cập nhật tồn dư hàng đợi M/G/1 Lindley (`self.queue_backlog_mb`) + phép đo survey `mt76`.
        """
        self.current_step += 1
        act = np.asarray(action, dtype=np.int64).reshape(-1)
        prev_obs = self.true_current_obs if self.true_current_obs is not None else self.current_obs

        metrics = self.evaluate_network_physics(
            prev_obs,
            act,
            prev_action=self.previous_action,
            aqm_enabled=self.aqm_enabled,
            sub_noise_dbm=self.true_ch_noise,
            base_ofdma_capacity=self.target_ofdma_base,
            demand_scale=self.target_demand_scale,
            queue_backlog_mb=self.queue_backlog_mb,
        )
        self.current_ch = int(np.clip(act[0], 0, len(NON_DFS_5G_CHANNELS) - 1))
        self.previous_action = act.copy()
        self.queue_backlog_mb = float(metrics.get("backlog_next_mb", 0.0))

        # Chuyển pha Semi-Markov với thời lượng ngẫu nhiên 15..55 bước
        self.regime_remaining_steps -= 1
        if self.regime_remaining_steps <= 0:
            self._sample_markov_regime()

        # Lấy mẫu giải tích chính xác quá trình Ornstein-Uhlenbeck cho 9 kênh phổ tại dt = 10.0s
        decay_rf = math.exp(-OU_THETA_RF * DT_STEP_SEC)
        std_rf = OU_SIGMA_STAT_RF * math.sqrt(1.0 - decay_rf * decay_rf)
        noise_drift = self.rng.normal(0.0, std_rf, size=9).astype(np.float32)
        self.true_ch_noise = np.clip(
            decay_rf * self.true_ch_noise + (1.0 - decay_rf) * self.target_ch_noise + noise_drift,
            -102.0,
            -51.0,
        ).astype(np.float32)

        # Lấy mẫu OU chính xác cho RSSI & mật độ STA ngoại sinh tại dt = 10.0s
        decay_rssi = math.exp(-OU_THETA_RSSI * DT_STEP_SEC)
        std_rssi = OU_SIGMA_STAT_RSSI * math.sqrt(1.0 - decay_rssi * decay_rssi)
        rssi_next = float(np.clip(
            decay_rssi * float(prev_obs[0]) + (1.0 - decay_rssi) * self.target_rssi + self.rng.normal(0.0, std_rssi),
            OBS_LOW[0],
            OBS_HIGH[0],
        ))

        decay_sta = math.exp(-OU_THETA_STA * DT_STEP_SEC)
        std_sta = OU_SIGMA_STAT_STA * math.sqrt(1.0 - decay_sta * decay_sta)
        sta_next = float(np.clip(
            decay_sta * float(prev_obs[2]) + (1.0 - decay_sta) * self.target_sta + self.rng.normal(0.0, std_sta),
            OBS_LOW[2],
            OBS_HIGH[2],
        ))

        # CẢM BIẾN PHY: mô phỏng đúng cơ chế khảo sát `iw survey dump [in use]` trên driver mt76
        survey_noise_dbm = measure_mt76_survey_noise(self.true_ch_noise, self.current_ch, int(np.clip(act[1], 0, 2)))
        noise_next = float(np.clip(survey_noise_dbm + self.rng.normal(0.0, 0.25), OBS_LOW[1], OBS_HIGH[1]))

        # PHẢN HỒI NHÂN QUẢ TẦNG MAC (75% trực tiếp từ kết quả thực thi `metrics` của Agent)
        retry_next = float(np.clip(0.25 * float(prev_obs[3]) + 0.75 * metrics["retry_rate"] + self.rng.normal(0.0, 0.012), OBS_LOW[3], OBS_HIGH[3]))
        pc_next = float(np.clip(0.25 * float(prev_obs[4]) + 0.75 * metrics["p_c"] + self.rng.normal(0.0, 0.012), OBS_LOW[4], OBS_HIGH[4]))
        air_next = float(np.clip(0.25 * float(prev_obs[5]) + 0.75 * metrics["airtime_next"] + self.rng.normal(0.0, 0.015), OBS_LOW[5], OBS_HIGH[5]))
        # Khi ở kênh sạch, ofdma phục hồi dần về năng lực của thiết bị thật (`target_ofdma_base`) trừ suy giảm va chạm
        ofdma_causal = float(np.clip(self.target_ofdma_base * (1.0 - 0.55 * metrics["p_c"] - 0.25 * metrics["retry_rate"]), 0.06, 0.95))
        ofdma_target_step = 0.5 * metrics["ofdma_next"] + 0.5 * ofdma_causal
        ofdma_next = float(np.clip(0.25 * float(prev_obs[6]) + 0.75 * ofdma_target_step + self.rng.normal(0.0, 0.012), OBS_LOW[6], OBS_HIGH[6]))

        true_next_obs = np.array(
            [rssi_next, noise_next, sta_next, retry_next, pc_next, air_next, ofdma_next, self.current_is_break],
            dtype=np.float32,
        )
        self.true_current_obs = true_next_obs
        self.current_obs = self._format_obs_output(true_next_obs)

        terminated = bool(self.current_step >= self.max_steps)
        info = dict(metrics)
        info["true_ch_noise"] = self.true_ch_noise.copy()
        info["current_ch"] = self.current_ch
        return self.current_obs.copy(), float(metrics["reward"]), terminated, False, info

    def simulate_future(
        self,
        current_real_state: np.ndarray,
        action: np.ndarray,
        sub_noise_dbm: Optional[np.ndarray] = None,
    ) -> float:
        metrics = self._original_evaluate_network_physics(
            current_real_state,
            action,
            prev_action=None,
            aqm_enabled=self.aqm_enabled,
            sub_noise_dbm=sub_noise_dbm,
            queue_backlog_mb=self.queue_backlog_mb,
        )
        return float(metrics["reward"])

    def oracle_action(
        self,
        current_real_state: np.ndarray,
        sub_noise_dbm: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        opt_act, _ = self.compute_optimal_targets(
            current_real_state, aqm_enabled=self.aqm_enabled, sub_noise_dbm=sub_noise_dbm,
            queue_backlog_mb=self.queue_backlog_mb,
        )
        return opt_act


DigitalTwinEnv = UltimateDigitalTwinEnv
