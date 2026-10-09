import gymnasium as gym # Thư viên dùng trong Học Tăng Cường (Reinforcement Learning)
from gymnasium import spaces
import numpy as np
import random

class WiFiEnv(gym.Env):
    """
    Môi trường RL điều khiển Router OpenWrt để tối ưu hóa Wi-Fi.
    Kế thừa kiến trúc Lớp bọc AI đa hình (Polymorphic RL Wrapper).
    """
    def __init__(self, router_ip, username, password):
        super(WiFiEnv, self).__init__()
        
        # 1. KHÔNG GIAN HÀNH ĐỘNG (ACTION SPACE) - Macro Actions
        # AI sẽ KHÔNG can thiệp vi mô (như tính toán ma trận ZF/MMSE) để tránh làm treo RAM Router.
        # Chúng ta thiết kế Hành động Vĩ mô (Macro-actions) gồm 2 trục chính:
        # - Trục 1 (Kênh truyền): Chọn 1 trong 3 kênh không chồng lấp (Non-overlapping) ở dải 2.4GHz: 1, 6, 11.
        #   - Trục 2 (Công suất phát - Tx Power): Chọn 3 mức (Thấp: 10dBm, Trung bình: 15dBm, Cao: 20dBm).
        # Tổng cộng: 3 x 3 = 9 hành động rời rạc (Discrete).
        self.action_space = spaces.Discrete(9)
        
        # 2. KHÔNG GIAN TRẠNG THÁI (OBSERVATION / STATE SPACE)
        # Đây là "cảm biến" của AI, lấy dữ liệu từ Datalogger (kết hợp Sniffer scapy và Controller paramiko)[cite: 37].
        # Ta chuẩn hóa (Min-Max Scaling) các giá trị về khoảng [0, 1] để tránh Gradient Explosion.
        
        # Vector trạng thái gồm 4 chiều:
        # state = [Client_Count, RSSI, Retry_Rate, Latency]
        #   Client Count (Mật độ User), 
        #   Avg RSSI (Suy hao không gian), 
        #   Retransmission Rate (Tỷ lệ đụng độ CSMA/CA & Trạm ẩn), 
        #   Latency (Độ trễ)
        
        self.observation_space = spaces.Box(
            low=np.array([0.0, 0.0, 0.0, 0.0]), 
            high=np.array([1.0, 1.0, 1.0, 1.0]), 
            dtype=np.float32
        )
        
        # 3. KẾT NỐI HỆ THẦN KINH (Modules)
        self.router_ip = router_ip
        self.username = username
        self.password = password
        
        # Khởi tạo các hàm Controller (paramiko) và Sniffer (scapy) ở đây 
        # để chuẩn bị cho việc tương tác vật lý.
        print("[*] Đã khởi tạo Môi trường WiFiEnv (Polymorphic RL Wrapper) thành công!")

    def reset(self, seed=None):
        """Hàm reset môi trường về trạng thái ban đầu mỗi khi bắt đầu 1 Episode mới."""
        super().reset(seed=seed)
        # Code thực thi: Gửi lệnh qua SSH ép Router về cấu hình mặc định (vd: Kênh 1, Tx Power 20dBm).
        # Trả về State ban đầu.
        initial_state = np.array([0.1, 0.8, 0.05, 0.1], dtype=np.float32) # Giả lập state
        return initial_state, {}
    
    def step(self, action):
        """Tạo môi trường giả lập: AI sẽ tương tác với dữ liệu ảo"""
        
        # Giả lập State sau khi thực hiện Action: TỐT
        simulated_next_stated = np.array([
            random.uniform(0.1, 0.5),   # User_count 
            random.uniform(0.6, 0.9),   # RSSI: tốt
            random.uniform(0.01, 0.05),   # Retry_Rate: thấp
            random.uniform(0.05, 0.1)    # Latency: thấp
            ], dtype=np.float32)
        
        # Tính toán REWARD
        throughput = 1 - simulated_next_stated[2] # 1 - Retry_Rate
        latency = simulated_next_stated[3]
        penalty = 1 if latency < 0.8 else 10 # Phạt nặng nếu độ trễ cao
        
        reward = throughput - latency - penalty
        self.current_step += 1
        terminated = bool(self.current_step >= 100)
        reduced = False
        
        return simulated_next_stated, reward, terminated, reduced, {}