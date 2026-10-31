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
from src.envs.digital_twin_env import UltimateDigitalTwinEnv
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

BEACON_INTS = [80, 300]
PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"

# Khởi tạo trạng thái mặc định ban đầu của các Router là Kênh 36 (idx 0), HE40 (idx 1)
previous_actions = {}
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
                state_array = np.array([
                    data["avg_rssi"],
                    data["p_c"],
                    data["retry_rate"],
                    data["airtime_util"],
                    data["sta_count"],
                    data["hour"],
                    data["minute"],
                    data["is_break_time"]
                ], dtype=np.float32)
                state_array = np.clip(
                    state_array,
                    a_min=[-90.0, 0.0, 0.0, 0.0, 0, 0, 0, 0],
                    a_max=[-30.0, 1.0, 1.0, 1.0, 300, 23, 59, 1]
                )
                states[bssid.upper()] = state_array
        return states
    except Exception as e:
        logging.error(f"Lỗi đọc JSON State: {e}")
        return {}


def execute_ai_action_for_ap(target_ip, bssid, state_arr, action_array, expected_reward):
    ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_pd_idx, beamform_idx = action_array

    target_channel = CHANNELS[ch_idx]
    target_bw = BANDWIDTHS[bw_idx]
    target_tx = 16 + (tx_idx % 5)  # Đảm bảo công suất phát mạnh mẽ 16..20 dBm
    target_mcs = mcs_idx
    target_beacon = BEACON_INTS[beacon_idx]
    target_beamform = True if beamform_idx == 1 else False

    logging.info(
        f"👉 Kê đơn cho AP {target_ip} ({TARGET_RADIO}/{TARGET_IFACE}): "
        f"CH={target_channel}, BW={target_bw}, Tx={target_tx}dBm, MCS={target_mcs}, Beacon={target_beacon}ms"
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
        enable_bf=target_beamform
    )
    actuators.apply_mcs_ceiling(target_ip, USER, PWD, TARGET_IFACE, target_mcs)

    status = "Executed" if success else "Failed"
    ai_logger.log_action(target_ip, bssid, state_arr, action_array, expected_reward, status)


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
                sta_cnt = int(state[4])
                p_c_val = float(state[1])
                airtime_val = float(state[3])

                logging.info(
                    f"📊 Khám AP {target_ip} [{bssid}] "
                    f"(STAs={sta_cnt}, RSSI={state[0]:.1f}dBm, p_c={p_c_val:.3f}, Airtime={airtime_val:.2%})"
                )

                action, _ = model.predict(state, deterministic=True)
                action = np.array(action, dtype=int)

                # 1. Điều phối Băng thông theo tải thực tế của từng AP:
                # Nếu AP đang chịu bão tải (p_c > 0.25 hoặc Airtime > 55% và có >= 2 STAs) -> Bóp về HE20 (idx 0)
                # Ngược lại nếu AP rảnh hoặc chỉ có 0-1 STA -> Mở rộng HE40/HE80 để tối đa hóa tốc độ!
                if sta_cnt >= 2 and (p_c_val > 0.25 or airtime_val > 0.55):
                    action[1] = 0  # HE20 chống nghẽn
                elif sta_cnt <= 1:
                    action[1] = 2  # HE80 cho AP ít thiết bị

                # 2. Bộ điều phối Kênh Đa Trạm (Orthogonal Multi-AP Channel Coordinator)
                ch_idx = int(action[0])
                if ch_idx in occupied_channels:
                    available_chs = [i for i in range(len(CHANNELS)) if i not in occupied_channels]
                    if available_chs:
                        action[0] = available_chs[0]
                        logging.info(f"   [Coordinator] Tự động tách Kênh cho {target_ip} sang CH={CHANNELS[action[0]]} để triệt tiêu CCI.")
                occupied_channels.append(int(action[0]))

                prev_action = previous_actions.get(bssid)
                # Chỉ kích hoạt đổi cấu hình khi thay đổi Kênh (action[0]) hoặc Băng thông (action[1])
                is_radio_changed = (prev_action is None) or (action[0] != prev_action[0]) or (action[1] != prev_action[1])

                if is_radio_changed:
                    # So sánh điểm thưởng của Action mới với cấu hình mặc định đang bị nghẽn (CH36=0, HE40=1)
                    baseline_action = prev_action if prev_action is not None else np.array([0, 1, 2, 7, 0, 2, 1])
                    oracle_twin.previous_action = None
                    current_reward = oracle_twin.simulate_future(state, baseline_action)
                    expected_reward = oracle_twin.simulate_future(state, action)
                    delta_gain = expected_reward - current_reward

                    logging.info(
                        f"   [Oracle] Điểm cấu hình mới: {expected_reward:.2f} "
                        f"(So với hiện tại: {delta_gain:+.2f})"
                    )

                    if expected_reward < -2.5 and delta_gain < 0.0:
                        logging.warning("   [CẢNH BÁO] Oracle phủ quyết lệnh này! Giữ nguyên cấu hình cũ.")
                        ai_logger.log_action(target_ip, bssid, state, action, expected_reward, "Vetoed")
                    else:
                        logging.info("   ✅ Lệnh tối ưu đã được Oracle phê duyệt. Đang thực thi xuống OpenWrt...")
                        execute_ai_action_for_ap(target_ip, bssid, state, action, expected_reward)
                        previous_actions[bssid] = action.copy()

            time.sleep(args.interval)

        except KeyboardInterrupt:
            print(f"\n[+] Đã dừng HIL Agent ({args.band}).")
            break
        except Exception as e:
            logging.error(f"Lỗi Vòng Lặp Chính: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()