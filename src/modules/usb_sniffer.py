import os 
import subprocess
import time
import threading
import csv
from datetime import datetime
from scapy.all import sniff
from scapy.layers.dot11 import Dot11, RadioTap, Dot11Beacon, Dot11Elt

# Cấu hình file xuất dữ liệu cho data_visualization.ipynb
HISTORY_CSV_PATH = "data/processed/telemetry_history_NEW.csv"
HISTORY_COLUMNS = [
    "Timestamp", "AP_MAC", "SSID", "Channel",
    "avg_rssi", "total_packets", "retry_packets", "p_c", "sta_count"
]

class USBSniffer:
    def __init__(self, iface="wlan0mon"):
        self.iface = iface
        self.channels = [1, 6, 11, 36, 40, 44, 48] # Các kênh 2.4GHz và 5GHz phổ biến
        self.ap_stats = {}
        self.lock = threading.Lock()
        self.current_channel = 1
        self.ssid_map = {}                  # Dictionary lưu ánh xạ BSSID -> {"ssid": str, "channel": int} thu được từ các gói Beacon
        self.stop_event = threading.Event() # Cờ đồng bộ đa luồng giúp báo hiệu cho tất cả các luồng nền dừng lại an toàn
        
        # Tạo thư mục và file CSV nếu chưa tồn tại
        os.makedirs(os.path.dirname(HISTORY_CSV_PATH), exist_ok=True)
        if not os.path.exists(HISTORY_CSV_PATH):
            with open(HISTORY_CSV_PATH, mode="w", newline="") as file:
                csv.writer(file).writerow(HISTORY_COLUMNS)

    def _channel_hopper(self):
        """Tầng PHY: Nhảy kênh liên tục để quét toàn bộ không gian vô tuyến"""
        print(f"[📡] Bắt đầu nhảy kênh trên {self.iface}...")
        while not self.stop_event.is_set():
            for ch in self.channels:
                if self.stop_event.is_set(): 
                    break
                subprocess.run(["iw", "dev", self.iface, "set", "channel", str(ch)], 
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.current_channel = ch
                self.stop_event.wait(0.5) # Dừng lại 0.5s ở mỗi kênh để bắt gói tin

    def _process_packet(self, _packet):
        """Tầng PHY & MAC: Bóc tách gói tin và trích xuất thông số"""
        # Bỏ qua các gói tin không mang chuẩn 802.11 (Dot11)
        if not _packet.haslayer(Dot11):
            return

        if _packet.type == 0 and _packet.subtype == 8:
            stats = _packet[Dot11Beacon].network_stats()
            bssid = _packet.addr2.upper()
        
        # Chỉ phân tích Data Frames (type == 2) để đo đụng độ thực tế
        if _packet.type == 2:
            # Tầng MAC: Lấy BSSID (Định danh AP) từ Address 1, 2 hoặc 3 tùy vào hướng cờ To-DS/From-DS
            """Đang chỉnh sửa lại _process_packet 28-11-2026"""
            if bssid == "FF:FF:FF:FF:FF:FF" or bssid == "UNKNOWN":
                return
            bssid = bssid.upper()

            with self.lock:
                if bssid not in self.ap_stats:
                    self.ap_stats[bssid] = {"total": 0, "retries": 0, "rssi_list": [], "channel": self.current_channel}
                
                self.ap_stats[bssid]["total"] += 1
                
                # Tầng MAC: Đọc cờ Retry trong FCfield (bit 0x08)
                if (_packet.FCfield & 0x08) != 0:
                    self.ap_stats[bssid]["retries"] += 1
                
                # Tầng PHY: Lấy RSSI từ RadioTap header
                if _packet.haslayer(RadioTap) and hasattr(_packet[RadioTap], 'dBm_AntSignal'):
                    rssi = _packet[RadioTap].dBm_AntSignal
                    # Sanity check: Chỉ lấy sóng hợp lý từ -95dBm đến -20dBm
                    if rssi is not None and -95 <= rssi <= -20:
                        self.ap_stats[bssid]["rssi_list"].append(rssi)

    def _etl_loop(self):
        """Luồng lưu trữ: Tính toán p_c và ghi vào CSV mỗi 10 giây"""
        print("[*] Luồng ghi dữ liệu CSV khởi động...")
        while True:
            time.sleep(10)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            with self.lock:
                if not self.ap_stats:
                    continue
                
                with open(HISTORY_CSV_PATH, mode="a", newline="") as file:
                    writer = csv.writer(file)
                    
                    for bssid, stats in self.ap_stats.items():
                        total = stats["total"]
                        if total == 0:
                            continue
                        
                        retries = stats["retries"]
                        # Tính xác suất đụng độ (Probability of Collision - p_c)
                        p_c = retries / total 
                        
                        # Tính RSSI trung bình trong chu kỳ
                        rssi_list = stats["rssi_list"]
                        avg_rssi = sum(rssi_list) / len(rssi_list) if rssi_list else -90.0
                        
                        # Ghi 1 dòng dữ liệu vào file CSV
                        writer.writerow([
                            now_str, bssid, stats["channel"], 
                            f"{avg_rssi:.2f}", total, retries, f"{p_c:.4f}"
                        ])
                
                # Reset bộ đếm cho chu kỳ tiếp theo
                self.ap_stats.clear()

    def run(self):
        # 1. Khởi động luồng nhảy kênh
        threading.Thread(target=self._channel_hopper, daemon=True).start()
        # 2. Khởi động luồng lưu dữ liệu
        threading.Thread(target=self._etl_loop, daemon=True).start()
        
        print("[+] Đang tiến hành Sniffing... (Nhấn Ctrl+C để dừng)")
        # 3. Chạy hàm sniff chính của Scapy ở luồng Main, không lưu RAM (store=0)
        sniff(iface=self.iface, prn=self._process_packet, store=0)

if __name__ == "__main__":
    # Đảm bảo bạn đã chạy script enable_monitor_mode.sh để kích hoạt wlan0mon trước
    sniffer = USBSniffer(iface="wlan0mon")
    try:
        sniffer.run()
    except KeyboardInterrupt:
        print("\n[🛑] Đã dừng thu thập dữ liệu vô tuyến.")