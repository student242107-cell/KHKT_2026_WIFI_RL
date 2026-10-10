import json
import os
import time
import threading
import re
import numpy as np
from scapy.all import sniff, Dot11
from datetime import datetime

class TelemetryPipeline:
    def __init__(self, iface_mon="wlan0mon"):
        """Khởi tạo các đường dẫn và biến đệm (buffer) cho dữ liệu."""
        self.iface_mon = iface_mon
        self.raw_tshark_path = "data/raw/tshark_raw.txt"
        self.processed_json_path = "data/processed/current_state.json"
        
        # Tự động tạo thư mục nếu chưa có
        os.makedirs(os.path.dirname(self.raw_tshark_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.processed_json_path), exist_ok=True)
        
        # Biến đệm lưu trữ dữ liệu Wi-Fi trong chu kỳ 30s
        self.packet_count = 0
        self.retry_count = 0
        self.rssi_list = []
        self.mac_set = set()
        
        # Khóa an toàn khi nhiều luồng (thread) cùng ghi/đọc biến
        self.lock = threading.Lock()

    def _scapy_packet_handler(self, pkt):
        """Callback: Bóc tách từng gói tin Wi-Fi bắt được từ không khí."""
        if pkt.haslayer(Dot11):
            with self.lock:
                self.packet_count += 1
                
                # Cờ FCfield & 0x08 kiểm tra bit "Retry" (Truyền lại do lỗi/va chạm)
                if pkt.FCfield & 0x08 != 0:
                    self.retry_count += 1
                    
                # RadioTap chứa thông tin vật lý (RSSI)
                if hasattr(pkt, 'dBm_AntSignal'):
                    self.rssi_list.append(pkt.dBm_AntSignal)
                    
                # Đếm số lượng địa chỉ MAC độc lập để ước tính STA_Count
                if pkt.addr2:
                    self.mac_set.add(pkt.addr2)

    def _parse_tshark_log(self):
        """Hàm cào dữ liệu (Scraping) từ file txt của Tshark."""
        try:
            if not os.path.exists(self.raw_tshark_path):
                return 0.0, 0.0 # Jitter, Loss mặc định
                
            with open(self.raw_tshark_path, 'r') as f:
                content = f.read()
                
                # Regex tìm cột "Max Jitter(ms)" và "Lost" trong bảng tshark rtp,streams
                # Ví dụ output: ... 5 (0.5%) ... 15.2 ... 5.5 ...
                jitter = 0.0
                loss_percent = 0.0
                
                # Tìm Jitter (thường nằm ở các cột cuối)
                jitter_matches = re.findall(r'(\d+\.\d+)', content)
                if jitter_matches:
                    jitter = float(jitter_matches[-1]) # Lấy giá trị float cuối cùng tạm thời làm Jitter
                    
                # Tìm Loss % (ví dụ: "(5.2%)")
                loss_matches = re.search(r'\((\d+\.\d+)%\)', content)
                if loss_matches:
                    loss_percent = float(loss_matches.group(1)) / 100.0
                    
                return jitter, loss_percent
        except Exception as e:
            print(f"[!] Lỗi đọc tshark log: {e}")
        return 0.0, 0.0

    def etl_loop(self):
        """Vòng lặp Transform & Load: Ép kiểu dữ liệu và lưu ra JSON mỗi 30s."""
        print("[*] Telemetry ETL Pipeline đang chạy ngầm...")
        while True:
            time.sleep(30) # Cửa sổ thời gian đo lường
            
            with self.lock:
                # 1. Transform: Tính toán các proxy metrics
                retry_rate = (self.retry_count / self.packet_count) if self.packet_count > 0 else 0.0
                avg_rssi = float(np.mean(self.rssi_list)) if self.rssi_list else -90.0
                sta_count = len(self.mac_set)
                
                jitter_ms, loss_rate = self._parse_tshark_log()
                
                # Ước tính xác suất va chạm p_c: Kết hợp Retry vô tuyến và Packet Loss mạng lõi
                p_c = min(1.0, retry_rate + loss_rate)
                # Ước tính Airtime (Giả sử băng thông trần 50Mbps)
                airtime_util = min(1.0, (self.packet_count * 1500 * 8) / (50 * 1e6))
                
                now = datetime.now()
                is_break_time = 1 if (now.minute in range(45, 60)) else 0
                
                # 2. Load: Đóng gói thành JSON chuẩn để AI đọc
                state_dict = {
                    "avg_rssi": avg_rssi,
                    "p_c": p_c,
                    "retry_rate": retry_rate,
                    "airtime_util": airtime_util,
                    "sta_count": sta_count,
                    "hour": now.hour,
                    "minute": now.minute,
                    "is_break_time": is_break_time
                }
                
                with open(self.processed_json_path, 'w') as f:
                    json.dump(state_dict, f)
                
                # Reset buffers cho chu kỳ 30s tiếp theo
                self.packet_count = 0
                self.retry_count = 0
                self.rssi_list.clear()
                self.mac_set.clear()

    def start(self):
        """Kích hoạt các tiểu trình (Threads) song song."""
        # Luồng 1: Scapy đánh hơi Wi-Fi (store=0 cực kỳ quan trọng để không tràn RAM)
        scapy_thread = threading.Thread(target=lambda: sniff(iface=self.iface_mon, prn=self._scapy_packet_handler, store=0), daemon=True)
        # Luồng 2: ETL xử lý dữ liệu và ghi JSON
        etl_thread = threading.Thread(target=self.etl_loop, daemon=True)
        
        scapy_thread.start()
        etl_thread.start()

# --- Chạy độc lập để test (Khi gõ python src/modules/telemetry.py) ---
if __name__ == "__main__":
    node = TelemetryPipeline(iface_mon="wlan0mon") # Đổi tên card mạng wifi monitor của bạn
    node.start()
    print("[+] Trạm viễn thông đang lắng nghe. Nhấn Ctrl+C để thoát.")
    while True: time.sleep(1)