import json
import os
import time
import threading
import re
import numpy as np
from scapy.all import sniff, Dot11
from datetime import datetime

class MultiAPTelemetryETL:
    def __init__(self, iface_mon="wlan0mon"):
        self.iface_mon = iface_mon
        self.raw_tshark_path = "data/raw/tshark_raw.txt"
        
        # Đường dẫn file JSON xuất ra (Per-AP State)
        self.processed_json_path = "data/processed/per_ap_state.json"
        
        os.makedirs(os.path.dirname(self.raw_tshark_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.processed_json_path), exist_ok=True)
        
        # --- CẤU TRÚC LƯU TRỮ MỚI: TÁCH BIỆT THEO TỪNG AP ---
        # Khóa (Key) của dictionary sẽ là địa chỉ MAC của AP (BSSID).
        # Ví dụ: {'MAC_AP1': {'packet_count': 100, 'retry_count': 5, 'rssi_list': [-60, -62], 'clients': set()}}
        self.ap_stats = {}
        
        self.lock = threading.Lock()

    def _scapy_handler(self, pkt):
        """Callback: Bóc tách gói tin Wi-Fi và ném vào 'ngăn kéo' của đúng AP đó"""
        if pkt.haslayer(Dot11):
            with self.lock:
                # addr3 trong 802.11 thường là BSSID (Địa chỉ MAC của Router/AP phát sóng)
                bssid = pkt.addr3
                client_mac = pkt.addr2
                
                # Bỏ qua các gói tin Broadcast hoặc rác không có BSSID rõ ràng
                if not bssid or bssid == "ff:ff:ff:ff:ff:ff":
                    return
                
                # Nếu phát hiện AP mới, tạo ngay một 'ngăn kéo' cho nó
                if bssid not in self.ap_stats:
                    self.ap_stats[bssid] = {
                        "packet_count": 0,
                        "retry_count": 0,
                        "rssi_list": [],
                        "clients": set()
                    }
                
                # --- CẬP NHẬT DỮ LIỆU CHO ĐÚNG CỤC AP ĐÓ ---
                self.ap_stats[bssid]["packet_count"] += 1
                
                # Đếm gói tin truyền lại (Va chạm)
                if pkt.FCfield & 0x08 != 0:
                    self.ap_stats[bssid]["retry_count"] += 1
                    
                # Thu thập RSSI
                if hasattr(pkt, 'dBm_AntSignal'):
                    self.ap_stats[bssid]["rssi_list"].append(pkt.dBm_AntSignal)
                    
                # Đếm số lượng Client gắn với AP này
                if client_mac:
                    self.ap_stats[bssid]["clients"].add(client_mac)

    def _channel_hopper(self, channels=[36, 40, 44, 48, 1, 6, 11]):
        """Tiểu trình ngầm: Ép USB Wi-Fi nhảy kênh liên tục để bao quát toàn trường"""
        print(f"[*] Radar Channel Hopper kích hoạt trên {self.iface_mon}")
        while True:
            for ch in channels:
                os.system(f"iwconfig {self.iface_mon} channel {ch}")
                time.sleep(0.5) 

    def _parse_tshark_log(self):
        """Hàm cào dữ liệu Jitter/Loss từ mạng lõi LAN (Chỉ số này bắt buộc phải dùng chung)"""
        try:
            if not os.path.exists(self.raw_tshark_path):
                return 0.0, 0.0 
                
            with open(self.raw_tshark_path, 'r') as f:
                content = f.read()
                jitter = 0.0
                loss_percent = 0.0
                
                jitter_matches = re.findall(r'(\d+\.\d+)', content)
                if jitter_matches:
                    jitter = float(jitter_matches[-1]) 
                    
                loss_matches = re.search(r'\((\d+\.\d+)%\)', content)
                if loss_matches:
                    loss_percent = float(loss_matches.group(1)) / 100.0
                    
                return jitter, loss_percent
        except Exception:
            pass
        return 0.0, 0.0

    def etl_loop(self):
        """Vòng lặp ETL: Tính toán và đóng gói State cho TỪNG AP MỘT"""
        print("[*] Per-AP Telemetry ETL Pipeline đang chạy ngầm...")
        while True:
            time.sleep(30) # Cửa sổ thời gian 30s
            
            with self.lock:
                now = datetime.now()
                is_break_time = 1 if (now.minute in range(45, 60)) else 0
                
                # Chỉ số mạng lõi lấy từ Tshark
                global_jitter, global_loss = self._parse_tshark_log()
                
                # Dictionary chứa kết quả cuối cùng sẽ xuất ra file JSON
                final_json_data = {}
                
                # --- KHÁM BỆNH CHO TỪNG AP ---
                for bssid, stats in self.ap_stats.items():
                    # Nếu AP này không có gói tin nào trong 30s qua, bỏ qua nó
                    if stats["packet_count"] == 0:
                        continue
                        
                    retry_rate = stats["retry_count"] / stats["packet_count"]
                    avg_rssi = float(np.mean(stats["rssi_list"])) if stats["rssi_list"] else -90.0
                    sta_count = len(stats["clients"])
                    
                    # Xác suất va chạm của riêng AP này = Retry của AP + Loss của toàn mạng
                    p_c = min(1.0, retry_rate + global_loss)
                    # Airtime bị chiếm dụng bởi riêng AP này
                    airtime_util = min(1.0, (stats["packet_count"] * 1500 * 8) / (50 * 1e6))
                    
                    # Đóng gói hồ sơ bệnh án cho AP này
                    final_json_data[bssid] = {
                        "avg_rssi": avg_rssi,
                        "p_c": p_c,
                        "retry_rate": retry_rate,
                        "airtime_util": airtime_util,
                        "sta_count": sta_count,
                        "hour": now.hour,
                        "minute": now.minute,
                        "is_break_time": is_break_time
                    }
                
                # Xuất file JSON
                with open(self.processed_json_path, 'w') as f:
                    json.dump(final_json_data, f)
                
                # Dọn dẹp 'ngăn kéo' cho chu kỳ tiếp theo
                self.ap_stats.clear()

    def start(self):
        threading.Thread(target=self._channel_hopper, daemon=True).start()
        threading.Thread(target=lambda: sniff(iface=self.iface_mon, prn=self._scapy_handler, store=0), daemon=True).start()
        threading.Thread(target=self.etl_loop, daemon=True).start()

if __name__ == "__main__":
    node = MultiAPTelemetryETL(iface_mon="wlan0mon") 
    node.start()
    print("[+] Trạm viễn thông Per-AP đang lắng nghe. Nhấn Ctrl+C để thoát.")
    while True: time.sleep(1)