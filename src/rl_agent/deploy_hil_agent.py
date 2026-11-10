import time
import numpy as np
import logging
import os
import json
import argparse
import yaml
from dotenv import load_dotenv
from stable_baselines3 import PPO

from src.modules import wifi_actuators as actuators
from src.modules import qoe_recorder
from src.envs.digital_twin_env import (
    UltimateDigitalTwinEnv, TX_POWER_DBM, OBSS_PD_OFFSETS, BEACON_INTS as TWIN_BEACON_INTS
)
from src.utils.logger import AILogger

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")
MODEL_PATH = "saved_models/best_model.zip"
CONFIG_PATH = "configs/network_config.yaml"

parser = argparse.ArgumentParser(description='Cognitive Wi-Fi HIL Agent')
parser.add_argument('--band', type=str, choices=['2.4G', '5G'], default='5G', help='Băng tần mục tiêu')
parser.add_argument('--interval', type=int, default=10, help='Chu kỳ điều khiển AI (giây)')
args = parser.parse_args()


def load_ap_map(band):
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
            topo = config.get('network_topology', {})
            key = 'ap_map' if band == '5G' else 'ap_map_2g'
            raw_map = topo.get(key, topo.get('ap_map', {}))
            return {k.upper(): v for k, v in raw_map.items()}
    except Exception as e:
        logging.error(f"Lỗi đọc file cấu hình mạng {CONFIG_PATH}: {e}")
        return {}


AP_MAP = load_ap_map(args.band)
if not AP_MAP:
    logging.critical("Không có thông tin AP nào trong file YAML!")
    exit(1)

logging.info(f"Đã nạp bản đồ mạng ({args.band}): Tối ưu hóa cho {len(AP_MAP)} APs: {AP_MAP}")

if args.band == '5G':
    TARGET_RADIO = "radio1"
    TARGET_IFACE = "phy1-ap0"
    CHANNELS = [36, 40, 44]
    BANDWIDTHS = ["HE20", "HE40", "HE80"]
else:
    TARGET_RADIO = "radio0"
    TARGET_IFACE = "phy0-ap0"
    CHANNELS = [1, 6, 11]
    BANDWIDTHS = ["HE20", "HE40", "HE40"]

BEACON_INTS = list(TWIN_BEACON_INTS)  # [80, 300] ms — đồng bộ với Digital Twin
PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"

# --- HYSTERESIS CHỐNG PING-PONG KHI TRIỂN KHAI THẬT ---
# Nhóm tham số "cần wifi reload" (kênh, băng thông, beacon, beamforming, OBSS-PD) chỉ được đổi khi:
#   (1) đề xuất mới ổn định DEBOUNCE_CYCLES chu kỳ liên tiếp, VÀ
#   (2) đã qua MIN_DWELL_CYCLES chu kỳ kể từ lần reload trước,
#   trừ khi mạng đang KHẨN CẤP (p_c > 0.6 hoặc Noise Floor > -70 dBm).
# Nhóm "áp nóng" (Tx Power, MCS ceiling) được áp ngay mỗi chu kỳ, không làm rớt kết nối.
DEBOUNCE_CYCLES = 2
MIN_DWELL_CYCLES = 3
# Kênh vừa bị bỏ vì nhiễu RF sẽ bị "cách ly" để tránh quay lại ngay khi Noise Floor
# trên kênh MỚI (đã sạch) khiến AI muốn về lại CH36.
CHANNEL_QUARANTINE_SEC = 1800
RELOAD_DIMS = (0, 1, 4, 5, 6)
HOT_DIMS = (2, 3)

previous_actions = {}
pending_actions = {}       # bssid -> (action, số chu kỳ liên tiếp)
last_reload_cycle = {}     # bssid -> cycle index
channel_quarantine = {}    # bssid -> {ch_idx: expiry_ts}
cycle_counter = 0
ai_logger = AILogger()


def get_per_ap_states():
    if not os.path.exists(PROCESSED_DATA_PATH):
        logging.warning("Chưa có dữ liệu Telemetry. Trả về rỗng.")
        return {}
    try:
        with open(PROCESSED_DATA_PATH, 'r') as f:
            json_data = json.load(f)

        states = {}
        for bssid, data in json_data.items():
            if bssid.upper() in AP_MAP:
                noise_val = data.get("noise_floor", data.get("phy_sensing", {}).get("noise_floor_dbm", -95.0))
                if noise_val is None:
                    noise_val = -95.0
                ofdma_val = data.get("ofdma_ratio", data.get("phy_sensing", {}).get("ofdma_ratio", 0.5))
                if ofdma_val is None:
                    ofdma_val = 0.5

                # Vector trạng thái 8 chiều Cross-Layer (PHY + MAC + Context)
                state_array = np.array([
                    float(data["avg_rssi"]),
                    float(noise_val),
                    float(data["sta_count"]),
                    float(data["retry_rate"]),
                    float(data["p_c"]),
                    float(data["airtime_util"]),
                    float(ofdma_val),
                    float(data.get("is_break_time", 0))
                ], dtype=np.float32)
                state_array = np.clip(
                    state_array,
                    a_min=[-90.0, -105.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    a_max=[-30.0, -50.0, 300.0, 1.0, 1.0, 1.0, 1.0, 1.0]
                )
                states[bssid.upper()] = state_array
        return states
    except Exception as e:
        logging.error(f"Lỗi đọc JSON State: {e}")
        return {}


def execute_ai_action_for_ap(target_ip, bssid, state_arr, action_array, expected_reward):
    try:
        ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_pd_idx, beamform_idx = [int(x) for x in action_array]

        target_channel = CHANNELS[ch_idx]
        target_bw = BANDWIDTHS[bw_idx]
        target_tx = int(TX_POWER_DBM[min(max(tx_idx, 0), len(TX_POWER_DBM) - 1)])  # 14..20 dBm (đơn điệu)
        target_mcs = mcs_idx
        target_beacon = BEACON_INTS[beacon_idx]
        target_beamform = True if beamform_idx == 1 else False
        target_obss_offset = OBSS_PD_OFFSETS[min(max(obss_pd_idx, 0), len(OBSS_PD_OFFSETS) - 1)]

        # Tính toán Dynamic Adaptive AQL dựa trên tải thực tế tầng MAC
        sta_cnt = int(state_arr[2]) if len(state_arr) > 2 else 1
        p_c_val = float(state_arr[4]) if len(state_arr) > 4 else 0.0
        airtime_val = float(state_arr[5]) if len(state_arr) > 5 else 0.0
        adaptive_aql = actuators.compute_adaptive_aql(
            bandwidth=target_bw,
            p_c=p_c_val,
            airtime_util=airtime_val,
            sta_count=sta_cnt
        )

        logging.info(
            f"👉 Kê đơn cho AP {target_ip} ({TARGET_RADIO}/{TARGET_IFACE}): "
            f"CH={target_channel}, BW={target_bw}, Tx={target_tx}dBm, MCS≤{target_mcs}, "
            f"Beacon={target_beacon}ms, OBSS-PD={-82 + target_obss_offset}dBm, "
            f"BF={'ON' if target_beamform else 'OFF'} | 🛡️ Dynamic AQL={adaptive_aql}us"
        )

        success = actuators.apply_ai_profile_atomic(
            ap_ip=target_ip,
            username=USER,
            password=PWD,
            radio_band=TARGET_RADIO,
            interface=TARGET_IFACE,
            channel=target_channel,
            bandwidth=target_bw,
            tx_power_dbm=target_tx,
            beacon_int=target_beacon,
            enable_bf=target_beamform,
            aql_limit=adaptive_aql,
            enable_atf=True,
            obss_pd_offset=target_obss_offset
        )
        actuators.apply_mcs_ceiling(target_ip, USER, PWD, TARGET_IFACE, target_mcs)

        status = "Executed" if success else "Failed"
        ai_logger.log_action(target_ip, bssid, state_arr, action_array, expected_reward, status)
        return success
    except Exception as e:
        logging.error(f"[-] Ngoại lệ trong execute_ai_action_for_ap cho AP {target_ip}: {e}")
        return False


def _is_emergency(state):
    """Mạng khẩn cấp -> bỏ qua dwell/debounce để phản ứng tức thì."""
    try:
        return float(state[4]) > 0.60 or float(state[1]) > -70.0
    except Exception:
        return False


def _best_allowed_channel(oracle_twin, state, action, forbidden):
    """Chọn kênh tốt nhất theo Oracle trong số các kênh không bị cách ly / chiếm dụng."""
    best_ch, best_r = None, -np.inf
    for c in range(len(CHANNELS)):
        if c in forbidden:
            continue
        cand = action.copy()
        cand[0] = c
        r = oracle_twin.simulate_future(state, cand)
        if r > best_r:
            best_ch, best_r = c, r
    return best_ch


def main():
    print("=========================================================")
    print(f"   HIL AGENT - TÍCH HỢP PREDICTIVE GUARDRAIL ({args.band}) ")
    print("=========================================================")

    try:
        custom_objects = {
            "learning_rate": 0.0,
            "lr_schedule": lambda _: 0.0,
            "clip_range": lambda _: 0.2,
        }
        model = PPO.load(MODEL_PATH, custom_objects=custom_objects, device="cpu")
        oracle_twin = UltimateDigitalTwinEnv()
    except Exception as e:
        logging.critical(f"[X] Không tìm thấy file trọng số hoặc lỗi khởi tạo Env: {e}")
        return

    # Khởi động QoE Recorder — ghi latency + packet loss liên tục khi Agent hoạt động
    gateway_ip = list(AP_MAP.values())[0] if AP_MAP else "192.168.2.1"
    qoe_recorder.start(gateway_ip=gateway_ip, interval=args.interval, ping_count=10)
    logging.info(f"[QoE] 📊 Recorder đang ghi dữ liệu → Xem biểu đồ: python visualize_qoe.py")

    while True:
        try:
            per_ap_states = get_per_ap_states()
            occupied_channels = []

            for bssid, state in per_ap_states.items():
                target_ip = AP_MAP[bssid]
                rssi_val = float(state[0])
                noise_val = float(state[1])
                sta_cnt = int(state[2])
                retry_val = float(state[3])
                p_c_val = float(state[4])
                airtime_val = float(state[5])
                ofdma_val = float(state[6])

                logging.info(
                    f"📊 Khám AP {target_ip} [{bssid}] "
                    f"(STAs={sta_cnt}, RSSI={rssi_val:.1f}dBm, Noise={noise_val:.1f}dBm, "
                    f"p_c={p_c_val:.3f}, Airtime={airtime_val:.2%}, OFDMA={ofdma_val:.1%})"
                )

                action, _ = model.predict(state, deterministic=True)
                action = np.array(action, dtype=int).reshape(-1)
                emergency = _is_emergency(state)
                now_ts = time.time()

                # 1. Cách ly kênh vừa bị bỏ vì nhiễu RF (PHY Noise Floor) -> chống ping-pong về CH36
                quarantine = channel_quarantine.setdefault(bssid, {})
                for c in [c for c, exp in quarantine.items() if exp < now_ts]:
                    quarantine.pop(c, None)
                prev_action = previous_actions.get(bssid)
                if prev_action is not None and int(action[0]) != int(prev_action[0]) and noise_val > -84.0:
                    quarantine[int(prev_action[0])] = now_ts + CHANNEL_QUARANTINE_SEC
                    logging.info(f"   [PHY-Sensing] Cách ly CH={CHANNELS[int(prev_action[0])]} "
                                 f"trong {CHANNEL_QUARANTINE_SEC // 60} phút (Noise={noise_val:.1f}dBm).")

                # 2. Bộ điều phối Kênh Đa Trạm (Orthogonal Multi-AP) + tôn trọng kênh bị cách ly
                forbidden = set(occupied_channels) | set(quarantine.keys())
                if int(action[0]) in forbidden:
                    alt = _best_allowed_channel(oracle_twin, state, action, forbidden)
                    if alt is None:  # hết kênh -> chỉ tránh kênh đang bị AP khác chiếm
                        alt = _best_allowed_channel(oracle_twin, state, action, set(occupied_channels))
                    if alt is not None and alt != int(action[0]):
                        logging.info(f"   [Coordinator] Đổi CH={CHANNELS[int(action[0])]} -> CH={CHANNELS[alt]} "
                                     f"(tránh CCI / kênh cách ly).")
                        action[0] = alt
                occupied_channels.append(int(action[0]))

                # 3. Hysteresis cho nhóm tham số cần wifi reload
                if prev_action is None:
                    reload_changed, hot_changed = True, True
                else:
                    reload_changed = any(int(action[d]) != int(prev_action[d]) for d in RELOAD_DIMS)
                    hot_changed = any(int(action[d]) != int(prev_action[d]) for d in HOT_DIMS)

                if reload_changed and prev_action is not None and not emergency:
                    key = tuple(int(action[d]) for d in RELOAD_DIMS)
                    p_key, p_cnt = pending_actions.get(bssid, (None, 0))
                    p_cnt = p_cnt + 1 if p_key == key else 1
                    pending_actions[bssid] = (key, p_cnt)
                    dwell_ok = (cycle_counter - last_reload_cycle.get(bssid, -10**9)) >= MIN_DWELL_CYCLES
                    if p_cnt < DEBOUNCE_CYCLES or not dwell_ok:
                        # Chưa đủ ổn định -> giữ nhóm reload cũ, vẫn cho phép áp nóng Tx/MCS
                        for d in RELOAD_DIMS:
                            action[d] = prev_action[d]
                        reload_changed = False
                        hot_changed = any(int(action[d]) != int(prev_action[d]) for d in HOT_DIMS)
                        logging.info(f"   [Hysteresis] Chờ xác nhận đề xuất reload ({p_cnt}/{DEBOUNCE_CYCLES}, "
                                     f"dwell={'OK' if dwell_ok else 'chưa đủ'}).")

                if not (reload_changed or hot_changed):
                    continue

                # 4. Oracle Guardrail: so sánh với cấu hình hiện tại
                baseline_action = prev_action if prev_action is not None else np.array([0, 1, 3, 7, 1, 0, 1])
                current_reward = oracle_twin.simulate_future(state, baseline_action)
                expected_reward = oracle_twin.simulate_future(state, action)
                delta_gain = expected_reward - current_reward
                logging.info(
                    f"   [Oracle] Điểm cấu hình mới: {expected_reward:.2f} "
                    f"(So với hiện tại: {delta_gain:+.2f})"
                )

                if prev_action is not None and expected_reward < -2.5 and delta_gain < 0.0:
                    logging.warning("   [CẢNH BÁO] Oracle phủ quyết lệnh này! Giữ nguyên cấu hình cũ.")
                    ai_logger.log_action(target_ip, bssid, state, action, expected_reward, "Vetoed")
                    continue

                logging.info("   ✅ Lệnh tối ưu đã được Oracle phê duyệt. Đang thực thi xuống OpenWrt...")
                if execute_ai_action_for_ap(target_ip, bssid, state, action, expected_reward):
                    previous_actions[bssid] = action.copy()
                    pending_actions.pop(bssid, None)
                    if reload_changed:
                        last_reload_cycle[bssid] = cycle_counter

            cycle_counter += 1
            time.sleep(args.interval)

        except KeyboardInterrupt:
            print(f"\n[+] Đã dừng HIL Agent ({args.band}).")
            break
        except Exception as e:
            logging.error(f"Lỗi Vòng Lặp Chính: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()