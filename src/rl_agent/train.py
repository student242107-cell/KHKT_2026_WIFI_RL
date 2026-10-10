# File: src/rl_agent/train.py
import sys
import os
# Đưa thư mục gốc vào đường dẫn để Python nhận diện thư mục src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.envs.wifi_env import WiFiEnv
from stable_baselines3 import PPO # Dùng thuật toán Proximal Policy Optimization

def train_simulated_ai():
    print("[*] Khởi tạo Môi trường Giả lập (Simulation Environment)...")
    env = WiFiEnv(router_ip="offline", username="mock", password="mock")
    
    print("[*] Khởi tạo Mô hình RL (Thuật toán PPO)...")
    # Đưa môi trường vào thuật toán PPO. MultiInputPolicy dành cho state dạng Box/Dict
    model = PPO("MlpPolicy", env, verbose=1, learning_rate=0.0005)
    
    print("[*] Bắt đầu tôi luyện AI (Training) trên 50,000 steps...")
    model.learn(total_timesteps=50000)
    
    print("[+] Hoàn tất! Lưu trí thông minh (Weights) vào thư mục saved_models/")
    model.save("saved_models/ppo_wifi_optimizer_v1")

if __name__ == "__main__":
    train_simulated_ai()