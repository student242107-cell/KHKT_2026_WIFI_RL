import os
import logging
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env
from dotenv import load_dotenv

# Import Môi trường Thực tế
from src.envs.real_router_env import RealRouterEnv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

load_dotenv()
ROUTER_IP = os.getenv("ROUTER_IP", "192.168.1.1")
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")

PRETRAINED_MODEL_PATH = "saved_models/best_model.zip"
FINE_TUNED_MODEL_PATH = "saved_models/best_model_real_world.zip"

def main():
    print("==========================================================")
    print(" 🚀 KHKT 2026: KHỞI ĐỘNG FINE-TUNING TRÊN PHẦN CỨNG THẬT")
    print("==========================================================")
    
    if not os.path.exists(PRETRAINED_MODEL_PATH):
        logging.critical("Không tìm thấy mô hình gốc. Hãy train trên Colab trước (train_offline.py)!")
        return

    # Khởi tạo Testbed Env
    env = RealRouterEnv(target_ap_ip=ROUTER_IP, router_user=USER, router_pass=PWD)
    
    # LOAD MÔ HÌNH CŨ
    logging.info("Đang nạp 'bộ não' Colab vào Môi trường Thực tế...")
    custom_objects = {
        "learning_rate": 3e-4,
        "lr_schedule": lambda _: 3e-4,
        "clip_range": lambda _: 0.2,
    }
    model = PPO.load(PRETRAINED_MODEL_PATH, env=env, device='cpu', custom_objects=custom_objects)

    # FINE-TUNING (HỌC BỔ SUNG)
    logging.info("Bắt đầu học tăng cường trên môi trường thật (Sẽ mất thời gian do phải chờ mạng Wi-Fi thực...).")
    # Lưu ý: 500 steps x 30 giây/step = khoảng 4 tiếng chạy thực tế.
    # Trong buổi test demo, bạn có thể chỉnh xuống 50 steps để kiểm tra luồng.
    model.learn(total_timesteps=50) 
    
    # LƯU MÔ HÌNH TỐI THƯỢNG
    model.save(FINE_TUNED_MODEL_PATH)
    logging.info(f"✅ ĐÃ LƯU MÔ HÌNH THỰC CHIẾN TẠI: {FINE_TUNED_MODEL_PATH}")

if __name__ == "__main__":
    main()