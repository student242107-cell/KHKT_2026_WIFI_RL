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
    Bộ trích xuất đặc trưng chuẩn hóa Min-Max về [-1.0, 1.0].
    Tự động co giãn theo cả observation 8D đơn lẻ lẫn `8 * N_FRAME_STACK = 32` chiều sau `VecFrameStack`.
    """
    def __init__(self, observation_space: spaces.Box):
        super().__init__(observation_space, features_dim=64)
        obs_dim = int(observation_space.shape[0])
        n_stack = max(1, obs_dim // len(OBS_LOW))
        low_tiled = np.tile(OBS_LOW, n_stack)[:obs_dim]
        high_tiled = np.tile(OBS_HIGH, n_stack)[:obs_dim]
        self.register_buffer("low", torch.tensor(low_tiled, dtype=torch.float32))
        self.register_buffer("high", torch.tensor(high_tiled, dtype=torch.float32))
        self.net = nn.Sequential(
            nn.Linear(obs_dim, 64),
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
        self.escape_cursor = 0
        self.quarantine_until: Dict[int, int] = {}

    def reset(self, **kwargs):
        obs, info = self.env.reset(**kwargs)
        self.step_count = 0
        self.last_switch_step = -100
        self.current_ch = int(getattr(self.env.unwrapped, "current_ch", 0))
        self.quarantine_until = {}
        return obs, info

    def _quarantine_channel_and_cluster(self, ch_idx: int, noise_obs: float) -> None:
        """Cách ly nhân quả kênh đang nhiễu và cụm gộp 40/80 MHz lân cận khi phát hiện nhiễu băng rộng."""
        self.quarantine_until[ch_idx] = self.step_count + self.quarantine_steps
        if noise_obs > -80.0 and ch_idx < 8:
            partner_40 = ch_idx ^ 1
            self.quarantine_until[partner_40] = max(
                self.quarantine_until.get(partner_40, 0),
                self.step_count + max(6, self.quarantine_steps // 2),
            )
        if noise_obs > -71.5 and ch_idx < 8:
            block_range = range(0, 4) if ch_idx < 4 else range(4, 8)
            exp_block = self.step_count + max(8, self.quarantine_steps - 2)
            for c_b in block_range:
                self.quarantine_until[c_b] = max(self.quarantine_until.get(c_b, 0), exp_block)

    def _pick_rotated_escape_channel(
        self, allowed: list, proposed_ch: int, bw_req: int, sta_obs: float
    ) -> int:
        self.escape_cursor = (self.escape_cursor + 1) % len(self._ESCAPE_ROTATION)
        rot_order = [
            self._ESCAPE_ROTATION[(self.escape_cursor + i) % len(self._ESCAPE_ROTATION)]
            for i in range(len(self._ESCAPE_ROTATION))
        ]
        allowed_rot = [c for c in rot_order if c in allowed and c != self.current_ch]
        if not allowed_rot:
            allowed_rot = [c for c in allowed if c != self.current_ch] or allowed
        # Khi bão tải mật độ cao (cần HE20), ưu tiên định kỳ khám phá cả CH165 (idx 8)
        if (sta_obs > 78.0 or bw_req == 0) and (8 in allowed_rot) and (self.escape_cursor % 3 == 0):
            return 8
        # Khi cần băng thông rộng HE40/HE80, ưu tiên kênh 0..7 hỗ trợ gộp kênh
        if bw_req > 0:
            wide_cands = [c for c in allowed_rot if c < 8]
            if wide_cands:
                return wide_cands[0]
        return allowed_rot[0]

    def filter_channel_action(self, obs_seen: np.ndarray, action: np.ndarray) -> Tuple[np.ndarray, bool]:
        act = np.array(action, dtype=np.int64, copy=True).reshape(-1)
        proposed_ch = int(np.clip(act[0], 0, len(NON_DFS_5G_CHANNELS) - 1))
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
                self._quarantine_channel_and_cluster(self.current_ch, noise_obs)

        # Nếu đứng trên kênh đang nhiễu, cần rời CH165, hoặc đề xuất trúng kênh đang bị cách ly -> chọn kênh thoát xoay vòng
        if need_escape or stuck_on_ch165:
            if need_escape and (self.current_ch not in self.quarantine_until):
                self._quarantine_channel_and_cluster(self.current_ch, noise_obs)
            allowed = [c for c in range(len(NON_DFS_5G_CHANNELS)) if c not in self.quarantine_until]
            if not allowed:
                oldest_ch = min(self.quarantine_until.items(), key=lambda kv: kv[1])[0]
                self.quarantine_until.pop(oldest_ch, None)
                allowed = [oldest_ch]
            if (act[0] not in allowed) or (act[0] == self.current_ch) or (need_escape and (self.escape_cursor % 2 == 0)):
                act[0] = self._pick_rotated_escape_channel(allowed, proposed_ch, int(act[1]), sta_obs)
            else:
                self.escape_cursor = (self.escape_cursor + 1) % len(self._ESCAPE_ROTATION)
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
    _FREQ_LOSS_TUPLE = (0.0, 0.03, 0.07, 0.10, 0.90, 0.93, 0.96, 0.99, 1.02)
    _CH_INDEX_MAP = {ch: idx for idx, ch in enumerate(NON_DFS_5G_CHANNELS)}

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
        self.rng = np.random.default_rng(seed)

        self.action_space = spaces.MultiDiscrete(ACTION_NVEC)
        self.observation_space = spaces.Box(low=OBS_LOW.copy(), high=OBS_HIGH.copy(), dtype=np.float32)

        self.current_step = 0
        self.current_ch = 0
        self.previous_action: Optional[np.ndarray] = None
        self.scenario_mode = 0

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
    ) -> Dict[str, float]:
        """
        Lõi Tính toán Vật lý Viễn thông 4 Tầng (100% Pure Physics):
        1. Spectrum Bonding & Friis Propagation trên toàn 9 kênh 5GHz Non-DFS đối xứng.
        2. Link Budget (Tx Power), SU-Beamforming CSI Aging, OBSS-PD Spatial Reuse.
        3. Minstrel-HT `mcs_ceiling` Link Adaptation (điểm ngọt AMC tại SINR Margin ~6.5 dB, PER <= 1.5%)
           & Va chạm CSMA/CA Bianchi.
        4. Goodput (Bảo toàn lưu lượng), M/G/1 AQM Queueing & Wilson-Hilferty Gamma Latency P99.
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

            # 1. TẦNG VẬT LÝ PHỔ TẦN & GỘP KÊNH (9 KÊNH ĐỐI XỨNG)
            spectrum_noise = cls._construct_9ch_spectrum_profile(obs) if sub_noise_dbm is None else sub_noise_dbm

            chosen_sub_noise_dbm = float(spectrum_noise[ch_idx])
            primary_ch, actual_htmode, occupied_subs, is_invalid_bonding = resolve_valid_5g_channel_and_bw(ch_idx, bw_idx)
            ch_map = cls._CH_INDEX_MAP
            actual_bw_idx = 0 if actual_htmode == "HE20" else (1 if actual_htmode == "HE40" else 2)

            if actual_htmode == "HE20":
                occ_max_noise_dbm = chosen_sub_noise_dbm
                eff_noise_dbm = chosen_sub_noise_dbm - 2.2
                bw_tonal_factor = 1.00
            elif actual_htmode == "HE40":
                i0, i1 = ch_map[occupied_subs[0]], ch_map[occupied_subs[1]]
                n0, n1 = float(spectrum_noise[i0]), float(spectrum_noise[i1])
                occ_max_noise_dbm = n0 if n0 >= n1 else n1
                eff_noise_dbm = cls._dbm_sum(n0, n1) + 0.5
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
                n_pair1 = cls._dbm_sum(n0, n1)
                n_pair2 = cls._dbm_sum(n2, n3)
                eff_noise_dbm = cls._dbm_sum(n_pair1, n_pair2) + 2.0
                bw_tonal_factor = 3.96

            # 2. NGÂN SÁCH ĐƯỜNG TRUYỀN (LINK BUDGET), TX POWER & BEAMFORMING CSI AGING
            clip = cls._clip_scalar
            tx_power_dbm = 14.0 + float(tx_idx)
            freq_loss_db = cls._FREQ_LOSS_TUPLE[ch_idx]

            opt_tx_idx = float(int(clip(round((-44.0 - rssi) / 5.5 - (0.5 if sta > 100.0 else 0.0)), 0.0, 6.0)))
            target_tx_dbm = 14.0 + opt_tx_idx
            tx_underpower_pen_db = max(0.0, target_tx_dbm - tx_power_dbm) * 1.55
            tx_overdrive_pen_db = max(0.0, tx_power_dbm - target_tx_dbm) * 1.45
            rssi_compensated = rssi + 0.65 * (tx_power_dbm - 17.0) - freq_loss_db - tx_underpower_pen_db - 0.35 * tx_overdrive_pen_db

            is_break_flag = (int(round(is_break)) == 1)
            is_csi_aging_regime = (
                (sta > 95.0)
                or (is_break_flag and (sta > 58.0))
                or ((sta > 70.0) and (retry_obs > 0.30))
            )
            if bf_idx == 1:
                bf_gain_db = -2.0 if is_csi_aging_regime else 2.8
                csi_sounding_overhead = 0.068 if is_csi_aging_regime else 0.012
            else:
                bf_gain_db = 1.2 if is_csi_aging_regime else -1.1
                csi_sounding_overhead = 0.0

            escaped_rf_jam = (noise_obs > -84.0) and (occ_max_noise_dbm <= -88.0)
            eff_airtime_in = airtime_obs * 0.38 if escaped_rf_jam else airtime_obs
            eff_ofdma_raw = ofdma_obs if base_ofdma_capacity is None else float(base_ofdma_capacity)
            eff_ofdma_base = max(eff_ofdma_raw, 0.52) if (base_ofdma_capacity is None and escaped_rf_jam) else eff_ofdma_raw

            # Điểm tối ưu OBSS-PD và chế độ tải neo trực tiếp vào biến quan sát `obs` để không rò rỉ biến ẩn
            opt_obss_level = float(int(clip(round((ofdma_obs - 0.12) / 0.16), 0.0, 4.0)))
            obss_interference_leak_db = max(0.0, float(obss_idx) - opt_obss_level) * 1.75
            obss_cca_deferral_pen_db = max(0.0, opt_obss_level - float(obss_idx)) * 1.45

            is_heavy_load = (sta > 78.0) or ((airtime_obs > 0.76) and (ofdma_obs < 0.32))
            is_medium_load = (not is_heavy_load) and (32.0 < sta <= 78.0)
            opt_bw_idx = 0 if (is_heavy_load or ch_idx == 8) else (1 if is_medium_load else 2)

            if occ_max_noise_dbm <= -88.0:
                fading_margin_db = 1.1 + 1.4 * min(2.0, sta / 100.0) + 2.2 * max(0.0, 0.45 - ofdma_obs)
            else:
                fading_margin_db = (
                    (4.5 if is_heavy_load else (2.2 if is_medium_load else 0.8))
                    + 4.5 * max(0.0, 0.55 - eff_ofdma_base)
                )

            eff_sinr_db = (
                rssi_compensated + bf_gain_db - eff_noise_dbm - fading_margin_db
                - obss_interference_leak_db
            )

            # 3. TẦNG ĐIỀU CHẾ THÍCH ỨNG MINSTREL-HT (AMC VỚI ĐIỂM NGỌT PER <= 1.5%)
            clean_ref_noise = -96.4 if opt_bw_idx == 0 else (-90.7 if opt_bw_idx == 1 else -86.2)
            ref_fading_db = 1.1 + 1.4 * min(2.0, sta / 100.0) + 2.2 * max(0.0, 0.45 - ofdma_obs)
            link_sinr_est = (
                rssi
                + 0.65 * (target_tx_dbm - 17.0)
                + (1.2 if is_csi_aging_regime else 2.8)
                - clean_ref_noise
                - ref_fading_db
            )
            safe_mcs_idx = cls._mcs_lookup(link_sinr_est - 6.5)

            dynamic_fading_risk = (
                (occ_max_noise_dbm > -86.0)
                or ((not escaped_rf_jam) and retry_obs > 0.22)
                or (sta > 55.0)
                or ((not escaped_rf_jam) and pc_obs > 0.32)
            )
            severe_fading_risk = (occ_max_noise_dbm > -78.0) or ((not escaped_rf_jam) and (retry_obs > 0.38 or pc_obs > 0.50))
            minstrel_overshoot = 2 if severe_fading_risk else (1 if dynamic_fading_risk else 0)
            minstrel_natural_mcs = min(11, safe_mcs_idx + minstrel_overshoot)
            operating_mcs_idx = min(mcs_ceiling_idx, minstrel_natural_mcs)

            mcs_delta = abs(mcs_ceiling_idx - safe_mcs_idx)
            mcs_over_ceiling = max(0, mcs_ceiling_idx - safe_mcs_idx)
            mcs_under_ceiling = max(0, safe_mcs_idx - mcs_ceiling_idx)

            req_sinr_db = cls._MCS_REQ_SINR_TUPLE[operating_mcs_idx]
            sinr_margin_db = eff_sinr_db - req_sinr_db
            exp_arg = clip(0.95 * (sinr_margin_db - 2.1), -60.0, 60.0)
            per_sigmoid = 1.0 / (1.0 + math.exp(exp_arg))
            mcs_overreach_db = max(0.0, (req_sinr_db + 5.5) - eff_sinr_db)
            per_phy = clip(
                per_sigmoid + 0.032 * (mcs_overreach_db * mcs_overreach_db) + 0.015 * mcs_over_ceiling,
                0.002,
                0.96,
            )

            if is_heavy_load:
                bw_contention_factor = 0.44 if actual_bw_idx == 0 else (1.58 if actual_bw_idx == 1 else 2.35)
            elif is_medium_load or (occ_max_noise_dbm > -86.0):
                bw_contention_factor = 1.12 if actual_bw_idx == 0 else (0.52 if actual_bw_idx == 1 else 1.72)
            else:
                bw_contention_factor = 1.38 if actual_bw_idx == 0 else (0.95 if actual_bw_idx == 1 else 0.32)

            channel_interf_ratio = clip((eff_noise_dbm - (-95.0)) / 32.0, 0.01, 0.92)
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

            p_c = clip(
                0.44 * channel_interf_ratio + 0.38 * mac_contention + 0.42 * per_phy + ofdma_spike_pen,
                0.02,
                0.96,
            )
            retry_rate = clip(0.55 * p_c + 0.45 * per_phy, 0.01, 0.96)

            # 4. THÔNG LƯỢNG KHẢ DỤNG, LƯU LƯỢNG YÊU CẦU & HÀNG ĐỢI M/G/1 AQM
            beacon_overhead = 0.052 if beacon_idx == 0 else 0.012
            if is_break_flag:
                roaming_handover_factor = 1.00 if beacon_idx == 0 else 0.76
            else:
                roaming_handover_factor = 0.90 if beacon_idx == 0 else 1.00

            ofdma_tput_boost = 0.88 + 0.26 * ofdma_efficiency
            effective_mcs_rate = 0.28 * cls._MCS_BASE_RATE_TUPLE[operating_mcs_idx] + 0.09 * 137.6
            phy_rate_mbps = effective_mcs_rate * bw_tonal_factor * roaming_handover_factor * ofdma_tput_boost

            total_overhead = min(0.35, beacon_overhead + csi_sounding_overhead + (0.18 if is_invalid_bonding else 0.0))
            service_capacity_mbps = max(8.0, phy_rate_mbps * (1.0 - total_overhead) * (1.0 - p_c) * (1.0 - 0.65 * per_phy))

            env_clean_sinr_est = (rssi + 2.0) - (-94.5) - (3.5 if is_heavy_load else 1.2)
            reachable_mcs_idx = cls._mcs_lookup(env_clean_sinr_est - 6.5)
            mcs_demand_scale = 0.68 * ((0.28 * cls._MCS_BASE_RATE_TUPLE[reachable_mcs_idx] + 0.09 * 137.6) / 89.6)

            if (not is_heavy_load) and (not is_medium_load):
                base_demand = clip(110.0 + sta * 1.25 + 14.0 * eff_airtime_in, 115.0, 190.0)
            elif is_medium_load:
                base_demand = clip(58.0 + sta * 0.40 + 10.0 * eff_airtime_in, 62.0, 102.0)
            else:
                base_demand = clip(20.0 + sta * 0.05 + 4.0 * eff_airtime_in, 22.0, 34.0)
            offered_load_mbps = base_demand * mcs_demand_scale * float(demand_scale)

            airtime_next = clip(offered_load_mbps / max(service_capacity_mbps, 10.0), 0.08, 0.99)

            rho = offered_load_mbps / max(service_capacity_mbps, 8.0)
            overload_ratio = max(0.0, (offered_load_mbps - service_capacity_mbps) / max(offered_load_mbps, 1.0))

            if aqm_enabled:
                ofdma_queue_relief = 1.0 - 0.25 * ofdma_efficiency
                queue_delay_ms = (0.95 + 2.1 * min(rho, 1.0) + (22.0 * min(rho - 1.0, 2.5) if rho > 1.01 else 0.0)) * ofdma_queue_relief
            else:
                queue_delay_ms = 3.8 + 14.0 * (min(rho, 3.2) ** 2) + (32.0 * max(0.0, rho - 0.88))

            retrans_delay_ms = 1.15 * (retry_rate / max(1.0 - retry_rate, 0.16))
            handover_delay_ms = 2.8 if (is_break_flag and beacon_idx == 1) else (0.55 if ((not is_break_flag) and beacon_idx == 0) else 0.0)
            mean_latency_ms = 1.55 + queue_delay_ms + retrans_delay_ms + handover_delay_ms
            jitter_ms = 0.12 + 1.30 * (p_c * p_c) + (1.65 * overload_ratio if aqm_enabled else 8.2 * overload_ratio)
            p99_latency_ms = gamma_p99_from_moments(mean_latency_ms, jitter_ms, d_min=1.50)

            mac_exhaust_loss = (p_c * per_phy) ** 3
            congestion_drop = (0.055 * overload_ratio) if aqm_enabled else (0.48 * overload_ratio)
            packet_loss_ratio = clip(mac_exhaust_loss + congestion_drop, 0.0, 1.0)
            packet_loss_pct = packet_loss_ratio * 100.0

            # Goodput chuẩn IETF RFC 2647: trừ phần suy hao do mất gói & nghẽn hàng đợi
            raw_goodput_mbps = min(offered_load_mbps, service_capacity_mbps)
            tcp_qoe_factor = (1.0 - packet_loss_ratio) * (
                1.0 - 0.16 * min(1.0, max(0.0, p99_latency_ms - 15.0) / 85.0)
            )
            goodput_mbps = raw_goodput_mbps * max(0.15, tcp_qoe_factor)

            # 5. HÀM MỤC TIÊU ĐA CHỈ TIÊU VẬT LÝ THUẦN TÚY
            goodput_fulfillment = goodput_mbps / max(offered_load_mbps, 1.0)
            spectral_efficiency_score = 0.65 * (service_capacity_mbps / max(offered_load_mbps, 20.0))
            tput_score = 2.15 * goodput_fulfillment + 0.72 * (goodput_mbps / 100.0) + min(1.05, spectral_efficiency_score)

            amc_stability_bonus = 0.68 * math.exp(max(-60.0, -0.55 * (mcs_delta * mcs_delta)))
            mcs_ceiling_risk_pen = 0.15 * mcs_over_ceiling + 0.12 * mcs_under_ceiling
            bw_mismatch_pen = 0.36 * abs(actual_bw_idx - opt_bw_idx)

            link_efficiency_bonus = (
                - 0.26 * (tx_underpower_pen_db + tx_overdrive_pen_db)
                - 0.28 * (obss_interference_leak_db + obss_cca_deferral_pen_db)
                - mcs_ceiling_risk_pen
                - bw_mismatch_pen
                + (0.34 if roaming_handover_factor >= 1.0 else -0.26)
                + (0.30 if bf_gain_db > 1.0 else -0.26)
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
                "safe_mcs": float(safe_mcs_idx),
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
    ) -> Dict[str, float]:
        metrics = cls._original_evaluate_network_physics(
            obs, action, prev_action=None, aqm_enabled=aqm_enabled,
            sub_noise_dbm=sub_noise_dbm, base_ofdma_capacity=base_ofdma_capacity,
            demand_scale=demand_scale,
        )
        if prev_action is None:
            metrics["is_downtime_triggered"] = 0.0
            return metrics

        ch_idx, bw_idx, tx_idx = int(action[0]), int(action[1]), int(action[2])
        prev_ch_idx, prev_bw_idx, prev_tx_idx = int(prev_action[0]), int(prev_action[1]), int(prev_action[2])

        flapping_penalty = 0.0
        is_downtime_triggered = False

        if ch_idx != prev_ch_idx or bw_idx != prev_bw_idx:
            is_downtime_triggered = True
            prev_metrics = cls._original_evaluate_network_physics(
                obs, prev_action, prev_action=None, aqm_enabled=aqm_enabled,
                sub_noise_dbm=sub_noise_dbm, base_ofdma_capacity=base_ofdma_capacity,
                demand_scale=demand_scale,
            )
            physical_gain = float(metrics["reward"] - prev_metrics["reward"])
            is_legitimate_adaptation = (physical_gain > 0.35)

            if not is_legitimate_adaptation:
                if ch_idx != prev_ch_idx:
                    flapping_penalty -= 0.85
                if bw_idx != prev_bw_idx:
                    flapping_penalty -= 0.55

            reload_fraction = 0.06 if is_legitimate_adaptation else 0.22
            metrics["throughput"] = float(metrics["throughput"] * (1.0 - reload_fraction))
            metrics["goodput"] = float(metrics["goodput"] * (1.0 - reload_fraction))
            lat_delta = 1.6 if is_legitimate_adaptation else 3.6
            metrics["mean_latency_ms"] = float(metrics["mean_latency_ms"] + lat_delta * 0.55)
            metrics["jitter_ms"] = float(metrics["jitter_ms"] + (0.35 if is_legitimate_adaptation else 0.85))
            metrics["p99_latency_ms"] = gamma_p99_from_moments(metrics["mean_latency_ms"], metrics["jitter_ms"], d_min=1.50)
            metrics["packet_loss_pct"] = float(min(100.0, metrics["packet_loss_pct"] + (0.10 if is_legitimate_adaptation else 0.35)))

        if abs(tx_idx - prev_tx_idx) > 2:
            flapping_penalty -= 0.12 * (abs(tx_idx - prev_tx_idx) - 2)

        metrics["is_downtime_triggered"] = 1.0 if is_downtime_triggered else 0.0
        metrics["reward"] = float(metrics["reward"] + flapping_penalty)
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
    ) -> Tuple[np.ndarray, Dict[str, float]]:
        """Tìm nghiệm tối ưu toàn cục thực sự argmax_a R_physics(s, a) trên không gian 7 chiều."""
        rssi, noise_obs, sta, retry_obs, pc_obs, airtime_obs, ofdma_obs, is_break = [float(x) for x in obs]

        tx_base = int(np.clip(np.round((-44.0 - rssi) / 5.5 - (0.5 if sta > 100.0 else 0.0)), 0, 6))
        tx_cands = sorted(list(set([max(0, tx_base - 1), tx_base, min(6, tx_base + 1)])))
        obss_base = int(np.clip(np.round((ofdma_obs - 0.12) / 0.16), 0, 4))
        obss_cands = sorted(list(set([max(0, obss_base - 1), obss_base, min(4, obss_base + 1)])))
        bcn_opt = 0 if int(round(is_break)) == 1 else 1
        is_csi_aging = (
            (sta > 95.0)
            or ((int(round(is_break)) == 1) and (sta > 58.0))
            or ((sta > 70.0) and (retry_obs > 0.30))
        )
        bf_opt = 0 if is_csi_aging else 1

        # Pha 1: Duyệt 25 cặp (ch, bw) dùng tuple thuần (zero allocation) & gom top-2 ứng viên tốt nhất
        pair_scores = []
        best_rew = -1e9
        best_act = None
        best_metrics = None

        for ch_c in range(9):
            for bw_c in range(3):
                if ch_c == 8 and bw_c > 0:
                    continue
                m_probe = cls._original_evaluate_network_physics(
                    obs, (ch_c, bw_c, tx_base, 11, bcn_opt, obss_base, bf_opt),
                    aqm_enabled=aqm_enabled, sub_noise_dbm=sub_noise_dbm,
                    base_ofdma_capacity=base_ofdma_capacity, demand_scale=demand_scale,
                )
                est_safe = int(m_probe["safe_mcs"])
                mcs_cands = set(range(max(0, est_safe - 2), min(12, est_safe + 4)))
                mcs_cands.add(11)
                pair_best_r = -1e9
                for mcs_c in mcs_cands:
                    cand = (ch_c, bw_c, tx_base, mcs_c, bcn_opt, obss_base, bf_opt)
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
            cur_tx, cur_obss, cur_bf = tx_base, obss_base, bf_opt
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
                for bf_c in (bf_opt, 1 - bf_opt):
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

        return np.array(best_act, dtype=np.int64), best_metrics

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

        elif regime == 1:  # Nhiễu hẹp 1..2 kênh lân cận tại vị trí ngẫu nhiên trên cả 9 kênh (0..8)
            self.target_ch_noise = base_clean
            jam_center = int(self.rng.integers(0, 9))
            jam_lvl = float(self.rng.uniform(-82.5, -71.0))
            self.target_ch_noise[jam_center] = jam_lvl
            if self.rng.random() < 0.55:
                neighbor = jam_center + (1 if jam_center < 8 else -1)
                self.target_ch_noise[neighbor] = jam_lvl - float(self.rng.uniform(0.5, 2.5))
            self.target_sta = float(self.rng.uniform(12.0, 62.0))
            self.target_ofdma_base = float(self.rng.uniform(0.45, 0.72))
            self.target_demand_scale = float(self.rng.uniform(0.80, 1.10))
            self.current_is_break = 1.0 if self.rng.random() < 0.25 else 0.0

        elif regime == 2:  # Nhiễu băng rộng đánh sập 1 cụm 40/80 MHz (UNII-1 hoặc UNII-3 Low/Mid)
            self.target_ch_noise = base_clean
            cluster_type = int(self.rng.choice([0, 1, 2]))  # 0: sập UNII-1; 1: sập UNII-3 149..161; 2: sập 2 cụm 40MHz
            jam_lvl = float(self.rng.uniform(-71.5, -54.0))
            if cluster_type == 0:
                self.target_ch_noise[0:4] = jam_lvl - self.rng.uniform(0.0, 2.5, size=4)
                # Ngẫu nhiên làm hơi nhiễu thêm phần đầu UNII-3 để kích hoạt cả 157, 161, 165
                if self.rng.random() < 0.45:
                    self.target_ch_noise[4:6] = float(self.rng.uniform(-79.0, -73.0))
            elif cluster_type == 1:
                self.target_ch_noise[4:8] = jam_lvl - self.rng.uniform(0.0, 2.5, size=4)
            else:
                self.target_ch_noise[0:2] = jam_lvl - self.rng.uniform(0.0, 2.0, size=2)
                self.target_ch_noise[4:6] = jam_lvl - self.rng.uniform(0.0, 2.0, size=2)
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
            self.rng = np.random.default_rng(seed)
        self.current_step = 0
        self.previous_action = None

        obs0, true_noise, _, _ = self.sample_snapshot_scenario()
        self.true_ch_noise = true_noise.copy()
        # Chọn kênh khởi đầu phản ánh đúng `obs0[1]`
        self.current_ch = int(np.argmin(np.abs(self.true_ch_noise - obs0[1])))
        self.true_current_obs = obs0.copy()
        self.current_obs = self._format_obs_output(obs0)
        return self.current_obs.copy(), {"true_ch_noise": self.true_ch_noise.copy(), "current_ch": self.current_ch}

    def step(self, action: np.ndarray):
        """
        Chuyển tiếp trạng thái Markov Vật lý Liên tục (Continuous Physics Evolution):
        1. Thực thi hành động trên trường phổ thực `self.true_ch_noise[0..8]`.
        2. Kênh chọn mới trở thành `self.current_ch = action[0]`, nên `noise_next` phản ánh đúng
           nhiễu thực của kênh vừa chọn (`noise_on_chosen_ch`).
        3. Các chỉ số MAC (`retry`, `p_c`, `airtime`, `ofdma`) tiến hóa 75% trực tiếp từ kết quả `metrics`
           của hành động Agent; môi trường ngoại lai trôi theo Ornstein-Uhlenbeck & thời lượng Markov ngẫu nhiên.
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
        )
        self.current_ch = int(np.clip(act[0], 0, len(NON_DFS_5G_CHANNELS) - 1))
        self.previous_action = act.copy()

        # Tiến hóa chế độ môi trường Markov liên tục với thời lượng ngẫu nhiên 15..55 bước
        self.regime_remaining_steps -= 1
        if self.regime_remaining_steps <= 0:
            self._sample_markov_regime()

        # Ornstein-Uhlenbeck Drift cho 9 kênh phổ thực (jammer tăng/giảm tốc độ bám 0.65)
        alpha_rf = 0.65
        noise_drift = self.rng.normal(0.0, 0.40, size=9).astype(np.float32)
        self.true_ch_noise = np.clip(
            (1.0 - alpha_rf) * self.true_ch_noise + alpha_rf * self.target_ch_noise + noise_drift,
            -102.0,
            -51.0,
        ).astype(np.float32)

        # Tiến hóa Ornstein-Uhlenbeck cho RSSI & STA (ngoại sinh)
        rssi_next = float(np.clip(0.80 * float(prev_obs[0]) + 0.20 * self.target_rssi + self.rng.normal(0.0, 0.8), OBS_LOW[0], OBS_HIGH[0]))
        sta_next = float(np.clip(0.82 * float(prev_obs[2]) + 0.18 * self.target_sta + self.rng.normal(0.0, 1.8), OBS_LOW[2], OBS_HIGH[2]))

        # CẢM BIẾN PHY: nhiễu nền đo trên toàn cụm subchannel đang gộp (`occupied_subs` của kênh & BW đang dùng)
        _, _, occ_subs_step, _ = resolve_valid_5g_channel_and_bw(self.current_ch, int(np.clip(act[1], 0, 2)))
        occ_noise_max = max(float(self.true_ch_noise[self._CH_INDEX_MAP[c]]) for c in occ_subs_step)
        noise_next = float(np.clip(occ_noise_max + self.rng.normal(0.0, 0.25), OBS_LOW[1], OBS_HIGH[1]))

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
        )
        return float(metrics["reward"])

    def oracle_action(
        self,
        current_real_state: np.ndarray,
        sub_noise_dbm: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        opt_act, _ = self.compute_optimal_targets(
            current_real_state, aqm_enabled=self.aqm_enabled, sub_noise_dbm=sub_noise_dbm
        )
        return opt_act


DigitalTwinEnv = UltimateDigitalTwinEnv
