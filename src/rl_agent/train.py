import logging
import os
from dotenv import load_dotenv
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecFrameStack

from src.envs.digital_twin_env import (
    N_FRAME_STACK,
    NormalizedWifiFeaturesExtractor,
    CrossLayerNormExtractor,
)
from src.envs.real_router_env import make_real_router_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

load_dotenv()
ROUTER_IP = os.getenv("ROUTER_IP", "192.168.2.1")
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS")

PRETRAINED_MODEL_PATH = "saved_models/best_model.zip"
FINE_TUNED_MODEL_PATH = "saved_models/best_model_real_world.zip"


def build_finetune_vec_env(
    router_ip: str = ROUTER_IP,
    router_user: str = USER,
    router_pass: str = PWD,
    hardware_type: str = "WIFI_6",
    step_interval: float = 10.0,
) -> VecFrameStack:
    """Khởi tạo môi trường RealRouterEnv đã bọc HardwareAwareWrapper + VecFrameStack(4) khớp best_model.zip."""
    dummy = DummyVecEnv(
        [
            lambda: make_real_router_env(
                hardware_type=hardware_type,
                target_ap_ip=router_ip,
                router_user=router_user,
                router_pass=router_pass,
                step_interval=step_interval,
            )
        ]
    )
    return VecFrameStack(dummy, n_stack=N_FRAME_STACK)


def main():
    print("==========================================================")
    print("   KHKT 2026: KHOI DONG FINE-TUNING TREN PHAN CUNG THAT")
    print("==========================================================")

    if not os.path.exists(PRETRAINED_MODEL_PATH):
        logging.critical(f"Không tìm thấy mô hình gốc tại {PRETRAINED_MODEL_PATH}. Hãy huấn luyện trên Digital Twin trước!")
        return

    vec_env = build_finetune_vec_env(
        router_ip=ROUTER_IP,
        router_user=USER,
        router_pass=PWD,
        hardware_type=os.getenv("ROUTER_HW_TYPE", "WIFI_6"),
    )

    logging.info("Đang nạp 'bộ não' Digital Twin (VecFrameStack=4, NormalizedWifiFeaturesExtractor) vào Môi trường Thực tế...")
    custom_objects = {
        "learning_rate": 1e-4,
        "lr_schedule": lambda _: 1e-4,
        "clip_range": lambda _: 0.2,
        "NormalizedWifiFeaturesExtractor": NormalizedWifiFeaturesExtractor,
        "CrossLayerNormExtractor": CrossLayerNormExtractor,
    }
    model = PPO.load(PRETRAINED_MODEL_PATH, env=vec_env, device="cpu", custom_objects=custom_objects)

    logging.info("Bắt đầu Fine-Tuning trực tiếp trên OpenWrt Testbed...")
    model.learn(total_timesteps=50, reset_num_timesteps=False)

    os.makedirs(os.path.dirname(FINE_TUNED_MODEL_PATH), exist_ok=True)
    model.save(FINE_TUNED_MODEL_PATH)
    logging.info(f"[+] ĐÃ LƯU MÔ HÌNH THỰC CHIẾN TẠI: {FINE_TUNED_MODEL_PATH}")


if __name__ == "__main__":
    main()
