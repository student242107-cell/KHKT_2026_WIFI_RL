import time
import numpy as np
import logging
import os
import json
import argparse
from dotenv import load_dotenv
from stable_baselines3 import PPO

from src.modules import wifi_actuators as actuators 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv() 

ROUTER_IP = os.getenv("ROUTER_IP", "192.168.1.1")
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")
MODEL_PATH = "saved_models/best_model.zip"

# Sử dụng Argparse để nhận tham số từ Terminal
parser = argparse.ArgumentParser(description='Cognitive Wi-Fi HIL Agent')
parser.add_argument('--band', type=str, choices=['2.4G', '5G'], default='5G',
                    help='Băng tần mục tiêu để tối ưu hóa (2.4G hoặc 5G)')
args = parser.parse_args()

# --- TỰ ĐỘNG CHUYỂN ĐỔI BĂNG TẦN DỰA TRÊN THAM SỐ ---
if args.band == '5G':
    TARGET_RADIO = "radio0" 
    TARGET_IFACE = "wlan0"
    CHANNELS = [36, 40, 44]
    BANDWIDTHS = ["HT20", "VHT40", "VHT80"]
    PROCESSED_DATA_PATH = "data/processed/current_state_5g.json"
    logging.info("🚀 Khởi động AI Agent cho băng tần 5GHz (radio0)")
else: # 2.4G
    TARGET_RADIO = "radio1" 
    TARGET_IFACE = "wlan1"
    CHANNELS = [1, 6, 11] # CHỈ dùng 3 kênh không chồng lấn này ở 2.4G
    BANDWIDTHS = ["HT20", "VHT40", "VHT40"] # Ép VHT80 về VHT40 cho 2.4G (hoặc ép cứng HT20 tùy kịch bản)
    PROCESSED_DATA_PATH = "data/processed/current_state_24g.json"
    logging.info("🚀 Khởi động AI Agent cho băng tần 2.4GHz (radio1)")

BEACON_INTS = [80, 300]
OBSS_PD_LEVELS = [-82, -77, -72, -67, -62] 


def get_real_time_state():
    if not os.path.exists(PROCESSED_DATA_PATH):
        logging.warning(f"Chưa tìm thấy dữ liệu Telemetry cho {args.band}. Trả về State an toàn.")
        return np.array([-70.0, 0.1, 0.05, 0.4, 20, 8, 15, 0], dtype=np.float32)
        
    try:
        with open(PROCESSED_DATA_PATH, 'r') as f:
            data = json.load(f)
            
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
        return state_array
        
    except Exception as e:
        logging.error(f"Lỗi giải mã JSON ({args.band}): {e}")
        return np.array([-70.0, 0.1, 0.05, 0.4, 20, 8, 15, 0], dtype=np.float32)


def execute_ai_action(action_array):
    ch_idx = action_array[0]
    bw_idx = action_array[1]
    tx_idx = action_array[2]
    mcs_idx = action_array[3]
    beacon_idx = action_array[4]
    obss_pd_idx = action_array[5] 
    beamform_idx = action_array[6] 
    
    target_channel = CHANNELS[ch_idx]
    target_bw = BANDWIDTHS[bw_idx]
    target_tx = 8 + tx_idx 
    target_beacon = BEACON_INTS[beacon_idx]
    target_obss_pd = OBSS_PD_LEVELS[obss_pd_idx]
    target_beamform = True if beamform_idx == 1 else False
    
    logging.info(f"👉 Lệnh Thực Thi: CH={target_channel}, BW={target_bw}, Tx={target_tx}dBm, MCS={mcs_idx}, Beacon={target_beacon}ms")

    # Lưu ý: Các hàm actuators của bạn cần được thiết kế để nhận tham số TARGET_RADIO và TARGET_IFACE
    actuators.adaptive_bandwidth_fallback(ROUTER_IP, USER, PWD, TARGET_RADIO, target_bw)
    
    # Hàm tx_power cũng cần sửa trong wifi_actuators.py để nhận TARGET_IFACE (wlan0 hoặc wlan1)
    actuators.optimize_tx_power(ROUTER_IP, USER, PWD, TARGET_IFACE, target_tx)
    
    actuators.apply_mcs_ceiling([ROUTER_IP], USER, PWD, TARGET_IFACE, mcs_limit=mcs_idx)
    
    # Beacon interval thường nằm trong cấu hình radio (radio0/radio1)
    actuators.adjust_beacon_interval([ROUTER_IP], USER, PWD, TARGET_RADIO, target_beacon)


def main():
    print("=========================================================")
    print(f"   KHỞI ĐỘNG HIL AGENT CHO BĂNG TẦN {args.band}       ")
    print("=========================================================")
    
    try:
        model = PPO.load(MODEL_PATH)
    except Exception as e:
        logging.critical(f"[X] Không tìm thấy file trọng số: {e}")
        return

    previous_action = None
    
    while True:
        try:
            state = get_real_time_state()
            action, _ = model.predict(state, deterministic=True)
            
            if previous_action is None or not np.array_equal(action, previous_action):
                execute_ai_action(action)
                previous_action = action.copy()
            else:
                pass # Chống spam log
                
            time.sleep(30)
            
        except KeyboardInterrupt:
            print(f"\n[+] Đã dừng HIL Agent ({args.band}).")
            break
        except Exception as e:
            logging.error(f"Lỗi Vòng Lặp Chính: {e}")
            time.sleep(5) 

if __name__ == "__main__":
    main()