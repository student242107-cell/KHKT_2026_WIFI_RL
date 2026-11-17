import argparse
from collections import deque
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import yaml
from dotenv import load_dotenv
from stable_baselines3 import PPO

from src.envs.digital_twin_env import (
    BANDWIDTHS,
    BEACON_INTS as TWIN_BEACON_INTS,
    CHANNELS_2G,
    CrossLayerNormExtractor,
    HardwareAwareWrapper,
    NON_DFS_5G_CHANNELS,
    N_FRAME_STACK,
    NormalizedWifiFeaturesExtractor,
    OBSS_PD_OFFSETS,
    OBS_HIGH,
    OBS_LOW,
    StatefulChannelGuardWrapper,
    TX_POWER_DBM,
    UltimateDigitalTwinEnv,
    build_stacked_obs,
    resolve_valid_5g_channel_and_bw,
)
from src.modules import qoe_recorder
from src.modules import wifi_actuators as actuators
from src.utils.logger import AILogger

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

load_dotenv()
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")
MODEL_PATH = "saved_models/best_model.zip"
CONFIG_PATH = "configs/network_config.yaml"
PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"

BEACON_INTS = list(TWIN_BEACON_INTS)  # [80, 300] ms — đồng bộ với Digital Twin

# --- HYSTERESIS CHỐNG PING-PONG & BẢO VỆ WIFI RELOAD KHI TRIỂN KHAI THẬT ---
DEBOUNCE_CYCLES = 2
MIN_DWELL_CYCLES = 3
CHANNEL_QUARANTINE_SEC = 1800
RELOAD_DIMS = (0, 1, 4, 5, 6)
HOT_DIMS = (2, 3)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cognitive Wi-Fi HIL Agent")
    parser.add_argument("--band", type=str, choices=["2.4G", "5G"], default="5G", help="Băng tần mục tiêu")
    parser.add_argument("--interval", type=int, default=10, help="Chu kỳ điều khiển AI (giây)")
    parser.add_argument(
        "--hardware",
        type=str,
        choices=["WIFI_6", "WIFI_5_AC", "WIFI_4_LEGACY"],
        default="WIFI_6",
        help="Chuẩn phần cứng OpenWrt AP",
    )
    parser.add_argument("--model", type=str, default=MODEL_PATH, help="Đường dẫn checkpoint PPO (.zip)")
    parser.add_argument("--dry-run", action="store_true", help="Chạy mô phỏng inference & guardrail không gửi SSH")
    return parser


def load_ap_map(band: str, config_path: str = CONFIG_PATH) -> Dict[str, str]:
    try:
        with open(config_path, "r") as file:
            config = yaml.safe_load(file)
            topo = config.get("network_topology", {})
            key = "ap_map" if band == "5G" else "ap_map_2g"
            raw_map = topo.get(key, topo.get("ap_map", {}))
            return {k.upper(): v for k, v in raw_map.items()}
    except Exception as e:
        logging.error(f"Lỗi đọc file cấu hình mạng {config_path}: {e}")
        return {}


def load_ppo_agent(model_path: str = MODEL_PATH) -> PPO:
    custom_objects = {
        "learning_rate": 0.0,
        "lr_schedule": lambda _: 0.0,
        "clip_range": lambda _: 0.2,
        "NormalizedWifiFeaturesExtractor": NormalizedWifiFeaturesExtractor,
        "CrossLayerNormExtractor": CrossLayerNormExtractor,
    }
    return PPO.load(model_path, custom_objects=custom_objects, device="cpu")


def get_per_ap_states(
    ap_map: Dict[str, str], processed_data_path: str = PROCESSED_DATA_PATH
) -> Dict[str, np.ndarray]:
    if not os.path.exists(processed_data_path):
        logging.warning("Chưa có dữ liệu Telemetry. Trả về rỗng.")
        return {}
    try:
        with open(processed_data_path, "r") as f:
            json_data = json.load(f)

        states: Dict[str, np.ndarray] = {}
        for bssid, data in json_data.items():
            if bssid.upper() in ap_map:
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
                states[bssid.upper()] = np.clip(state_array, OBS_LOW, OBS_HIGH).astype(np.float32)
        return states
    except Exception as e:
        logging.error(f"Lỗi đọc JSON State: {e}")
        return {}


def _is_emergency(state: np.ndarray) -> bool:
    """Mạng khẩn cấp -> bỏ qua dwell/debounce để phản ứng tức thì."""
    try:
        return float(state[4]) > 0.60 or float(state[1]) > -70.0
    except Exception:
        return False


def decode_action_for_band(action_array: np.ndarray, band: str) -> Dict[str, Any]:
    channels = list(NON_DFS_5G_CHANNELS) if band == "5G" else list(CHANNELS_2G)
    bw_list = list(BANDWIDTHS) if band == "5G" else ["HE20", "HE40", "HE40"]

    ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_pd_idx, beamform_idx = [int(x) for x in action_array]
    ch_idx = int(np.clip(ch_idx, 0, len(channels) - 1))
    bw_idx = int(np.clip(bw_idx, 0, len(bw_list) - 1))

    if band == "5G":
        target_channel, target_bw, _, bw_capped = resolve_valid_5g_channel_and_bw(ch_idx, bw_idx)
        if bw_capped:
            bw_idx = 0
    else:
        target_channel = channels[ch_idx]
        target_bw = bw_list[bw_idx]

    tx_idx = int(np.clip(tx_idx, 0, len(TX_POWER_DBM) - 1))
    mcs_idx = int(np.clip(mcs_idx, 0, 11))
    beacon_idx = int(np.clip(beacon_idx, 0, len(BEACON_INTS) - 1))
    obss_pd_idx = int(np.clip(obss_pd_idx, 0, len(OBSS_PD_OFFSETS) - 1))
    beamform_idx = int(np.clip(beamform_idx, 0, 1))

    return {
        "exec_action": np.array([ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_pd_idx, beamform_idx], dtype=np.int64),
        "channel": target_channel,
        "bandwidth": target_bw,
        "tx_power_dbm": int(TX_POWER_DBM[tx_idx]),
        "mcs_ceiling": mcs_idx,
        "beacon_int": int(BEACON_INTS[beacon_idx]),
        "obss_pd_offset": int(OBSS_PD_OFFSETS[obss_pd_idx]),
        "enable_bf": bool(beamform_idx == 1),
    }


def execute_ai_action_for_ap(
    target_ip: str,
    bssid: str,
    state_arr: np.ndarray,
    action_array: np.ndarray,
    expected_reward: float,
    band: str,
    target_radio: str,
    target_iface: str,
    ai_logger: Optional[AILogger] = None,
    dry_run: bool = False,
) -> bool:
    try:
        cfg = decode_action_for_band(action_array, band=band)
        adaptive_aql = actuators.compute_adaptive_aql(
            bandwidth=cfg["bandwidth"],
            p_c=float(state_arr[4]) if len(state_arr) > 4 else 0.0,
            airtime_util=float(state_arr[5]) if len(state_arr) > 5 else 0.0,
            sta_count=int(state_arr[2]) if len(state_arr) > 2 else 1,
        )

        logging.info(
            f"[HIL-EXEC{' DRY' if dry_run else ''}] AP {target_ip} ({target_radio}/{target_iface}): "
            f"CH={cfg['channel']}, BW={cfg['bandwidth']}, Tx={cfg['tx_power_dbm']}dBm, "
            f"MCS<={cfg['mcs_ceiling']}, Beacon={cfg['beacon_int']}ms, "
            f"OBSS-PD={-82 + cfg['obss_pd_offset']}dBm, BF={'ON' if cfg['enable_bf'] else 'OFF'} "
            f"| Dynamic AQL={adaptive_aql}us"
        )

        if dry_run:
            success = True
        else:
            success = actuators.apply_ai_profile_atomic(
                ap_ip=target_ip,
                username=USER,
                password=PWD,
                radio_band=target_radio,
                interface=target_iface,
                channel=cfg["channel"],
                bandwidth=cfg["bandwidth"],
                tx_power_dbm=cfg["tx_power_dbm"],
                beacon_int=cfg["beacon_int"],
                enable_bf=cfg["enable_bf"],
                aql_limit=adaptive_aql,
                enable_atf=True,
                obss_pd_offset=cfg["obss_pd_offset"],
            )
            actuators.apply_mcs_ceiling(target_ip, USER, PWD, target_iface, cfg["mcs_ceiling"])

        if ai_logger is not None:
            status = "Executed" if success else "Failed"
            ai_logger.log_action(target_ip, bssid, state_arr, cfg["exec_action"], expected_reward, status)
        return success
    except Exception as e:
        logging.error(f"[-] Ngoại lệ trong execute_ai_action_for_ap cho AP {target_ip}: {e}")
        return False


class HILCoordinator:
    """
    Bộ điều phối Pure PPO + VecFrameStack(4) + StatefulChannelGuardWrapper + HardwareAwareWrapper.
    Loại bỏ hoàn toàn bước Oracle Veto sai lệch trên kênh đơn lẻ; giữ lại phòng tránh CCI đa AP và Hysteresis OpenWrt.
    """

    def __init__(self, model: PPO, band: str = "5G", hardware_type: str = "WIFI_6"):
        self.model = model
        self.band = band
        self.channels = list(NON_DFS_5G_CHANNELS) if band == "5G" else list(CHANNELS_2G)
        self.hw_wrapper = HardwareAwareWrapper(
            UltimateDigitalTwinEnv(hardware_type=hardware_type),
            hardware_type=hardware_type,
        )
        self.expected_obs_dim = int(model.observation_space.shape[0])

        self.obs_buffers: Dict[str, deque] = {}
        self.guards: Dict[str, StatefulChannelGuardWrapper] = {}
        self.previous_actions: Dict[str, np.ndarray] = {}
        self.pending_actions: Dict[str, Tuple[Tuple[int, ...], int]] = {}
        self.last_reload_cycle: Dict[str, int] = {}
        self.channel_quarantine: Dict[str, Dict[int, float]] = {}
        self.cycle_counter = 0

    def _prepare_model_input(self, bssid: str, state: np.ndarray) -> np.ndarray:
        buf = self.obs_buffers.get(bssid)
        if buf is None:
            buf = deque([np.zeros(8, dtype=np.float32)] * (N_FRAME_STACK - 1) + [state.copy()], maxlen=N_FRAME_STACK)
            self.obs_buffers[bssid] = buf
        else:
            buf.append(state.copy())

        if self.expected_obs_dim == 8 * N_FRAME_STACK:
            return build_stacked_obs(buf, N_FRAME_STACK)
        return state.astype(np.float32)

    def _get_guard(self, bssid: str) -> StatefulChannelGuardWrapper:
        if bssid not in self.guards:
            self.guards[bssid] = StatefulChannelGuardWrapper(
                HardwareAwareWrapper(UltimateDigitalTwinEnv())
            )
        return self.guards[bssid]

    def decide_for_ap(
        self,
        bssid: str,
        state: np.ndarray,
        occupied_channels: List[int],
        now_ts: Optional[float] = None,
    ) -> Tuple[np.ndarray, bool, bool, float]:
        if now_ts is None:
            now_ts = time.time()

        model_obs = self._prepare_model_input(bssid, state)
        raw_act, _ = self.model.predict(model_obs, deterministic=True)
        raw_act = np.array(raw_act, dtype=np.int64).reshape(-1)

        quarantine = self.channel_quarantine.setdefault(bssid, {})
        for c in [k for k, exp in quarantine.items() if exp < now_ts]:
            quarantine.pop(c, None)

        prev_action = self.previous_actions.get(bssid)
        if self.band == "5G":
            guard = self._get_guard(bssid)
            guard.step_count = self.cycle_counter
            if prev_action is not None:
                guard.current_ch = int(prev_action[0])
            for c_q in quarantine:
                guard.quarantine_until.setdefault(c_q, self.cycle_counter + guard.quarantine_steps)
            guarded_act, _ = guard.filter_channel_action(state, raw_act)
            action = self.hw_wrapper.action(guarded_act)
        else:
            action = raw_act.copy()
            action[0] = int(np.clip(action[0], 0, len(self.channels) - 1))

        # Tránh trùng kênh đa AP (Orthogonal Multi-AP) và tránh nhảy vào kênh đang cách ly
        forbidden = set(occupied_channels) | set(quarantine.keys())
        if int(action[0]) in forbidden:
            allowed = [c for c in range(len(self.channels)) if c not in forbidden]
            if not allowed:
                allowed = [c for c in range(len(self.channels)) if c not in set(occupied_channels)]
            if allowed:
                alt = allowed[int(raw_act[0]) % len(allowed)]
                logging.info(
                    f"   [Coordinator] Đổi CH={self.channels[int(action[0])]} -> CH={self.channels[alt]} "
                    f"(tránh CCI / kênh cách ly)."
                )
                action[0] = alt

        if self.band == "5G":
            _, _, _, bw_capped = resolve_valid_5g_channel_and_bw(int(action[0]), int(action[1]))
            if bw_capped:
                action[1] = 0

        if prev_action is None:
            reload_changed, hot_changed = True, True
        else:
            reload_changed = any(int(action[d]) != int(prev_action[d]) for d in RELOAD_DIMS)
            hot_changed = any(int(action[d]) != int(prev_action[d]) for d in HOT_DIMS)

        emergency = _is_emergency(state)
        if reload_changed and prev_action is not None and not emergency:
            key = tuple(int(action[d]) for d in RELOAD_DIMS)
            p_key, p_cnt = self.pending_actions.get(bssid, (None, 0))
            p_cnt = p_cnt + 1 if p_key == key else 1
            self.pending_actions[bssid] = (key, p_cnt)
            dwell_ok = (self.cycle_counter - self.last_reload_cycle.get(bssid, -10**9)) >= MIN_DWELL_CYCLES
            if p_cnt < DEBOUNCE_CYCLES or not dwell_ok:
                for d in RELOAD_DIMS:
                    action[d] = prev_action[d]
                reload_changed = False
                hot_changed = any(int(action[d]) != int(prev_action[d]) for d in HOT_DIMS)
                logging.info(
                    f"   [Hysteresis] Chờ xác nhận reload ({p_cnt}/{DEBOUNCE_CYCLES}, "
                    f"dwell={'OK' if dwell_ok else 'chưa đủ'})."
                )

        # Chỉ cách ly kênh cũ khi quyết định chuyển kênh thực sự vượt qua Hysteresis
        if prev_action is not None and int(action[0]) != int(prev_action[0]) and float(state[1]) > -84.0:
            quarantine[int(prev_action[0])] = now_ts + CHANNEL_QUARANTINE_SEC
            logging.info(
                f"   [PHY-Sensing] Cách ly CH={self.channels[int(prev_action[0])]} "
                f"trong {CHANNEL_QUARANTINE_SEC // 60} phút (Noise={float(state[1]):.1f}dBm)."
            )

        if self.band == "5G":
            guard = self._get_guard(bssid)
            if prev_action is not None and int(action[0]) != int(prev_action[0]):
                guard.last_switch_step = self.cycle_counter + 1
            guard.current_ch = int(action[0])

        # Chỉ tính điểm reward thụ động để ghi log quan sát (tuyệt đối KHÔNG phủ quyết hành động AI)
        measured_noise = np.full(len(NON_DFS_5G_CHANNELS), -95.0, dtype=np.float32)
        if self.band == "5G":
            measured_noise[int(action[0])] = float(state[1])
        else:
            measured_noise[0] = float(state[1])
        expected_reward = float(
            UltimateDigitalTwinEnv.evaluate_network_physics(
                state, action, prev_action=prev_action, aqm_enabled=True, sub_noise_dbm=measured_noise
            )["reward"]
        )
        return action, reload_changed, hot_changed, expected_reward


def main(argv: Optional[List[str]] = None):
    args = build_arg_parser().parse_args(argv)
    ap_map = load_ap_map(args.band)
    if not ap_map:
        logging.critical("Không có thông tin AP nào trong file YAML!")
        raise SystemExit(1)

    target_radio = "radio1" if args.band == "5G" else "radio0"
    target_iface = "phy1-ap0" if args.band == "5G" else "phy0-ap0"
    logging.info(f"Đã nạp bản đồ mạng ({args.band}): {len(ap_map)} APs -> {ap_map}")

    print("=========================================================")
    print(f"   HIL AGENT - PURE PPO + STATEFUL GUARDRAIL ({args.band} / {args.hardware})")
    print("=========================================================")

    try:
        model = load_ppo_agent(args.model)
        coordinator = HILCoordinator(model=model, band=args.band, hardware_type=args.hardware)
        ai_logger = AILogger()
    except Exception as e:
        logging.critical(f"[X] Lỗi nạp mô hình PPO hoặc khởi tạo Coordinator: {e}")
        return

    gateway_ip = list(ap_map.values())[0] if ap_map else "192.168.2.1"
    qoe_recorder.start(gateway_ip=gateway_ip, interval=args.interval, ping_count=10)
    logging.info("[QoE] Recorder đang ghi dữ liệu -> Xem biểu đồ: python visualize_qoe.py")

    while True:
        try:
            per_ap_states = get_per_ap_states(ap_map)
            occupied_channels: List[int] = []

            for bssid, state in per_ap_states.items():
                target_ip = ap_map[bssid]
                logging.info(
                    f"[*] Khám AP {target_ip} [{bssid}] "
                    f"(STAs={int(state[2])}, RSSI={float(state[0]):.1f}dBm, Noise={float(state[1]):.1f}dBm, "
                    f"p_c={float(state[4]):.3f}, Airtime={float(state[5]):.2%}, OFDMA={float(state[6]):.1%})"
                )

                action, reload_changed, hot_changed, expected_reward = coordinator.decide_for_ap(
                    bssid=bssid,
                    state=state,
                    occupied_channels=occupied_channels,
                )
                occupied_channels.append(int(action[0]))

                if not (reload_changed or hot_changed):
                    continue

                if execute_ai_action_for_ap(
                    target_ip=target_ip,
                    bssid=bssid,
                    state_arr=state,
                    action_array=action,
                    expected_reward=expected_reward,
                    band=args.band,
                    target_radio=target_radio,
                    target_iface=target_iface,
                    ai_logger=ai_logger,
                    dry_run=args.dry_run,
                ):
                    coordinator.previous_actions[bssid] = action.copy()
                    coordinator.pending_actions.pop(bssid, None)
                    if reload_changed:
                        coordinator.last_reload_cycle[bssid] = coordinator.cycle_counter

            coordinator.cycle_counter += 1
            time.sleep(args.interval)

        except KeyboardInterrupt:
            print(f"\n[+] Đã dừng HIL Agent ({args.band}).")
            break
        except Exception as e:
            logging.error(f"Lỗi Vòng Lặp Chính: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
