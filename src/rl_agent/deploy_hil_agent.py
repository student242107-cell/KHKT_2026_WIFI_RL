import time
import numpy as np
import logging
import os
import json
import argparse
import yaml # THÊM DÒNG NÀY
from dotenv import load_dotenv
from stable_baselines3 import PPO

from src.modules import wifi_actuators as actuators 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv() 
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")
MODEL_PATH = "saved_models/best_model.zip"
CONFIG_PATH = "configs/network_config.yaml" # ĐƯỜNG DẪN TỚI FILE YAML

# --- HÀM TỰ ĐỘNG NẠP BẢN ĐỒ MẠNG TỪ YAML ---
def load_ap_map():
    try:
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
            # Trích xuất từ điển ap_map ra
            return config.get('network_topology', {}).get('ap_map', {})
    except Exception as e:
        logging.error(f"Lỗi đọc file cấu hình mạng {CONFIG_PATH}: {e}")
        return {}

# Gọi hàm để lấy danh sách AP
AP_MAP = load_ap_map()
if not AP_MAP:
    logging.critical("Không có thông tin AP nào trong file YAML. Hãy chạy scripts/auto_discover_aps.py trước!")
    exit(1)

# In ra màn hình để kiểm chứng
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

# Biến toàn cục để tránh việc AI gửi lệnh SSH liên tục nếu môi trường không đổi
previous_actions = {}


def get_per_ap_states():
    """Đọc file JSON và trả về từ điển chứa State của từng AP."""
    if not os.path.exists(PROCESSED_DATA_PATH):
        logging.warning("Chưa có dữ liệu Telemetry. Trả về rỗng.")
        return {}
        
    try:
        with open(PROCESSED_DATA_PATH, 'r') as f:
            json_data = json.load(f)
            
        states = {}
        for bssid, data in json_data.items():
            # Chỉ lấy các BSSID thuộc về hệ thống trường học của mình (bỏ qua mạng nhà hàng xóm)
            if bssid.upper() in AP_MAP:
                state_array = np.array([
                    data["avg_rssi"],
                    data["p_c"],
                    data["retry_rate"],
                    data["airtime_util"],
                    data["sta_count"], # Số Client kết nối vào riêng AP này
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
    """
    Biên dịch mảng Action và gửi lệnh SSH ĐẾN ĐÚNG CỤC AP bị sự cố.
    """
    ch_idx, bw_idx, tx_idx, mcs_idx, beacon_idx, obss_pd_idx, beamform_idx = action_array
    
    target_channel = CHANNELS[ch_idx]
    target_bw = BANDWIDTHS[bw_idx]
    target_tx = 8 + tx_idx 
    target_beacon = BEACON_INTS[beacon_idx]
    target_obss_pd = OBSS_PD_LEVELS[obss_pd_idx]
    target_beamform = True if beamform_idx == 1 else False
    
    logging.info(f"👉 Kê đơn cho AP {target_ip}: CH={target_channel}, BW={target_bw}, Tx={target_tx}dBm")

    # KÊ ĐƠN THUỐC ĐÍCH DANH (Gửi tham số target_ip vào các hàm SSH)
    actuators.adaptive_bandwidth_fallback(target_ip, USER, PWD, TARGET_RADIO, target_bw)
    actuators.optimize_tx_power(target_ip, USER, PWD, TARGET_IFACE, target_tx)
    actuators.apply_mcs_ceiling([target_ip], USER, PWD, TARGET_IFACE, mcs_limit=mcs_idx)
    actuators.adjust_beacon_interval([target_ip], USER, PWD, TARGET_RADIO, target_beacon)


def main():
    print("=========================================================")
    print(f"   HIL AGENT - CHẾ ĐỘ 'BÁC SĨ TỪNG PHÒNG' ({args.band})  ")
    print("=========================================================")
    
    try:
        model = PPO.load(MODEL_PATH)
    except Exception as e:
        logging.critical(f"[X] Không tìm thấy file trọng số: {e}")
        return
    
    while True:
        try:
            # 1. Bác sĩ cầm bệnh án của tất cả các phòng lên
            per_ap_states = get_per_ap_states()
            
            # 2. Khám từng phòng một
            for bssid, state in per_ap_states.items():
                target_ip = AP_MAP[bssid] # Ánh xạ MAC sang IP để biết đường truyền SSH
                logging.info(f"📊 Khám AP {target_ip} (Clients={int(state[4])}, p_c={state[1]:.2f})")
                
                # 3. Não bộ PPO đưa ra quyết định (Toa thuốc) cho phòng này
                action, _ = model.predict(state, deterministic=True)
                
                # 4. Kiểm tra xem phòng này có cần uống thuốc mới không?
                prev_action = previous_actions.get(bssid)
                if prev_action is None or not np.array_equal(action, prev_action):
                    logging.warning(f"⚠️ AP {target_ip} có sự cố hoặc cần tối ưu. Đang kê đơn thuốc...")
                    execute_ai_action_for_ap(target_ip, action)
                    previous_actions[bssid] = action.copy() # Lưu lại toa thuốc đã uống
                else:
                    pass # Phòng vẫn ổn, giữ nguyên phác đồ cũ.
                    
            time.sleep(30) # 30 giây sau đi khám lại một vòng
            
        except KeyboardInterrupt:
            print(f"\n[+] Đã dừng HIL Agent ({args.band}).")
            break
        except Exception as e:
            logging.error(f"Lỗi Vòng Lặp Chính: {e}")
            time.sleep(5) 

if __name__ == "__main__":
    main()