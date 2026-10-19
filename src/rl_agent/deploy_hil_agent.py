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
from src.envs.digital_twin_env import UltimateDigitalTwinEnv

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

# Ánh xạ chuẩn xác cho phần cứng Xiaomi AX3000T (OpenWrt MediaTek Filogic 820)
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
OBSS_PD_LEVELS = [-82, -77, -72, -67, -62]
PROCESSED_DATA_PATH = "data/processed/per_ap_state.json"

previous_actions = {}


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


def execute_ai_action_for_ap(target_ip, action_array):
    ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_pd_idx, beamform_idx = action_array

    target_channel = CHANNELS[ch_idx]
    target_bw = BANDWIDTHS[bw_idx]
    target_tx = 14 + tx_idx
    target_beacon = BEACON_INTS[beacon_idx]
    target_beamform = True if beamform_idx == 1 else False

    logging.info(
        f"👉 Kê đơn cho AP {target_ip} ({TARGET_RADIO}/{TARGET_IFACE}): "
        f"CH={target_channel}, BW={target_bw}, Tx={target_tx}dBm, Beacon={target_beacon}ms"
    )

    actuators.apply_ai_profile_atomic(
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


def main():
    print("=========================================================")
    print(f"   HIL AGENT - TÍCH HỢP PREDICTIVE GUARDRAIL ({args.band}) ")
    print("=========================================================")

    try:
        model = PPO.load(MODEL_PATH)
        oracle_twin = UltimateDigitalTwinEnv()
    except Exception as e:
        logging.critical(f"[X] Không tìm thấy file trọng số hoặc lỗi khởi tạo Env: {e}")
        return

    while True:
        try:
            per_ap_states = get_per_ap_states()

            for bssid, state in per_ap_states.items():
                target_ip = AP_MAP[bssid]
                logging.info(
                    f"📊 Khám AP {target_ip} [{bssid}] "
                    f"(STAs={int(state[4])}, RSSI={state[0]:.1f}dBm, p_c={state[1]:.3f}, Airtime={state[3]:.2%})"
                )

                action, _ = model.predict(state, deterministic=True)

                prev_action = previous_actions.get(bssid)
                if prev_action is None or not np.array_equal(action, prev_action):
                    oracle_twin.previous_action = prev_action
                    expected_reward = oracle_twin.simulate_future(state, action)

                    logging.info(f"   [Oracle] Điểm thưởng dự đoán tương lai: {expected_reward:.2f}")

                    if expected_reward < 0.0:
                        logging.warning("   [CẢNH BÁO] Oracle phủ quyết lệnh này! Giữ nguyên cấu hình cũ.")
                    else:
                        logging.info("   ✅ Lệnh an toàn. Đang thực thi xuống OpenWrt...")
                        execute_ai_action_for_ap(target_ip, action)
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