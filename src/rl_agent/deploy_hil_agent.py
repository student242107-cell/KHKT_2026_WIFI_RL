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
# --- IMPORT DIGITAL TWIN ĐỂ LÀM NHÀ TIÊN TRI ---
from src.envs.digital_twin_env import UltimateDigitalTwinEnv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv() 
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")
MODEL_PATH = "saved_models/best_model.zip"
CONFIG_PATH = "configs/network_config.yaml" 

# --- HÀM TỰ ĐỘNG NẠP BẢN ĐỒ MẠNG TỪ YAML ---
def load_ap_map():
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
            return config.get('network_topology', {}).get('ap_map', {})
    except Exception as e:
        logging.error(f"Lỗi đọc file cấu hình mạng {CONFIG_PATH}: {e}")
        return {}

AP_MAP = load_ap_map()
if not AP_MAP:
    logging.critical("Không có thông tin AP nào trong file YAML. Hãy chạy scripts/auto_discover_aps.py trước!")
    exit(1)

logging.info(f"Đã nạp bản đồ mạng: Tối ưu hóa cho {len(AP_MAP)} điểm phát sóng (APs).")

parser = argparse.ArgumentParser(description='Cognitive Wi-Fi HIL Agent')
parser.add_argument('--band', type=str, choices=['2.4G', '5G'], default='5G', help='Băng tần mục tiêu')
args = parser.parse_args()

if args.band == '5G':
    TARGET_RADIO = "radio0" 
    TARGET_IFACE = "wlan0"
    CHANNELS = [36, 40, 44]
    BANDWIDTHS = ["HT20", "VHT40", "VHT80"]
else: 
    TARGET_RADIO = "radio1" 
    TARGET_IFACE = "wlan1"
    CHANNELS = [1, 6, 11] 
    BANDWIDTHS = ["HT20", "VHT40", "VHT40"] 

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
    target_tx = 8 + tx_idx 
    target_beacon = BEACON_INTS[beacon_idx]
    target_obss_pd = OBSS_PD_LEVELS[obss_pd_idx]
    target_beamform = True if beamform_idx == 1 else False
    
    logging.info(f"👉 Kê đơn cho AP {target_ip}: CH={target_channel}, BW={target_bw}, Tx={target_tx}dBm")

    actuators.adaptive_bandwidth_fallback(target_ip, USER, PWD, TARGET_RADIO, target_bw)
    actuators.optimize_tx_power(target_ip, USER, PWD, TARGET_IFACE, target_tx)
    actuators.apply_mcs_ceiling([target_ip], USER, PWD, TARGET_IFACE, mcs_limit=mcs_idx)
    actuators.adjust_beacon_interval([target_ip], USER, PWD, TARGET_RADIO, target_beacon)


def main():
    print("=========================================================")
    print(f"   HIL AGENT - TÍCH HỢP PREDICTIVE GUARDRAIL ({args.band}) ")
    print("=========================================================")
    
    try:
        model = PPO.load(MODEL_PATH)
        # --- KHỞI TẠO DIGITAL TWIN LÀM NHÀ TIÊN TRI ---
        oracle_twin = UltimateDigitalTwinEnv()
    except Exception as e:
        logging.critical(f"[X] Không tìm thấy file trọng số hoặc lỗi khởi tạo Env: {e}")
        return
    
    while True:
        try:
            per_ap_states = get_per_ap_states()
            
            for bssid, state in per_ap_states.items():
                target_ip = AP_MAP[bssid] 
                logging.info(f"📊 Khám AP {target_ip} (Clients={int(state[4])}, p_c={state[1]:.2f})")
                
                # Não bộ PPO đưa ra đề xuất cấu hình
                action, _ = model.predict(state, deterministic=True)
                
                prev_action = previous_actions.get(bssid)
                if prev_action is None or not np.array_equal(action, prev_action):
                    
                    # --- VẬN HÀNH "SIMULATED GUARDRAIL" ĐỂ BẢO VỆ MẠNG ---
                    oracle_twin.previous_action = prev_action
                    expected_reward = oracle_twin.simulate_future(state, action)
                    
                    logging.info(f"   [Oracle] Điểm thưởng dự đoán tương lai: {expected_reward:.2f}")
                    
                    if expected_reward < 0.0:
                        logging.warning(f"   [CẢNH BÁO] Oracle phủ quyết lệnh này! Rủi ro quá tải mạng. Giữ nguyên cấu hình cũ.")
                    else:
                        logging.info(f"   ✅ Lệnh an toàn. Đang kê đơn thuốc...")
                        execute_ai_action_for_ap(target_ip, action)
                        previous_actions[bssid] = action.copy() 
                else:
                    pass 
                    
            time.sleep(30) 
            
        except KeyboardInterrupt:
            print(f"\n[+] Đã dừng HIL Agent ({args.band}).")
            break
        except Exception as e:
            logging.error(f"Lỗi Vòng Lặp Chính: {e}")
            time.sleep(5) 

if __name__ == "__main__":
    main()