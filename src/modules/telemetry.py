import json
import os
import time
import threading
import re
import numpy as np
from scapy.all import sniff
from scapy.layers.dot11 import Dot11
from datetime import datetime
import csv

# Địa chỉ MAC của cổng Default Gateway (Cổng nối ra Internet của trường)
# Bạn nhớ sửa thành MAC của cổng WAN/LAN Router của bạn nhé!
GATEWAY_MAC = "00:15:5d:d8:73:d3".upper()

class MultiAPTelemetryETL:
    def __init__(self, iface_mon="wlan0mon"):
        self.iface_mon = iface_mon
        self.raw_tshark_path = "data/raw/tshark_raw.txt"
        self.processed_json_path = "data/processed/per_ap_state.json"
        
        # [THÊM MỚI] Đường dẫn lưu dữ liệu lịch sử cho biểu đồ
        self.history_csv_path = "data/processed/telemetry_history.csv" 
        
        os.makedirs(os.path.dirname(self.raw_tshark_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.processed_json_path), exist_ok=True)
        
        # [THÊM MỚI] Tạo file CSV và ghi tiêu đề nếu file chưa tồn tại
        if not os.path.exists(self.history_csv_path):
            with open(self.history_csv_path, mode='w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Hour", "Minute", "AP_MAC", "avg_rssi", "p_c", "retry_rate", "airtime_util", "sta_count"])
                
        # Ngăn kéo hồ sơ bệnh án cho từng AP (BSSID)
        self.ap_stats = {}
        self.lock = threading.Lock()

    def _parse_mac_frame(self, pkt):
        """Hàm chuẩn mực IEEE 802.11 để bóc tách địa chỉ MAC dựa trên ToDS/FromDS"""
        dot11 = pkt.getlayer(Dot11)
        fc_field = int(dot11.FCfield)
        to_ds = bool(fc_field & 0x1)
        from_ds = bool(fc_field & 0x2)
        
        src_addr, dst_addr, bssid = None, None, None
        
        if not to_ds and not from_ds:    # Ad-hoc / Management
            dst_addr, src_addr, bssid = dot11.addr1, dot11.addr2, dot11.addr3
        elif to_ds and not from_ds:      # Uplink (STA -> AP)
            bssid, src_addr, dst_addr = dot11.addr1, dot11.addr2, dot11.addr3
        elif not to_ds and from_ds:      # Downlink (AP -> STA)
            dst_addr, bssid, src_addr = dot11.addr1, dot11.addr2, dot11.addr3
        elif to_ds and from_ds:          # WDS / Mesh
            dst_addr, src_addr = dot11.addr3, getattr(dot11, 'addr4', None)
            bssid = dot11.addr1 # Ước lượng trong WDS
            
        return (bssid.upper() if bssid else None, 
                src_addr.upper() if src_addr else None, 
                dst_addr.upper() if dst_addr else None)

    def _scapy_handler(self, pkt):
        """Bóc tách gói tin: Phân phòng (AP) và Phân bệnh (Internet/Local)"""
        if pkt.haslayer(Dot11) and pkt.type == 2: # Chỉ xét Data Frame
            
            bssid, src_mac, dst_mac = self._parse_mac_frame(pkt)
            
            # Bỏ qua nếu không rõ BSSID
            if not bssid or bssid == "FF:FF:FF:FF:FF:FF":
                return
                
            with self.lock:
                if bssid not in self.ap_stats:
                    # Cấu trúc ngăn kéo mới: Tách bạch Internet và Local
                    self.ap_stats[bssid] = {
                        "internet_data": 0, "internet_retry": 0,
                        "local_data": 0, "local_retry": 0,
                        "rssi_list": [], "clients": set()
                    }
                
                # Kiểm tra cờ Retry (Đụng độ)
                is_retry = (pkt.FCfield & 0x08) != 0
                
                # --- PHÂN LẠI LUỒNG DỮ LIỆU CHUẨN XÁC ---
                # Nếu nguồn hoặc đích là Gateway -> Đó là Internet Traffic
                if src_mac == GATEWAY_MAC or dst_mac == GATEWAY_MAC:
                    self.ap_stats[bssid]["internet_data"] += 1
                    if is_retry: self.ap_stats[bssid]["internet_retry"] += 1
                else: # Ngược lại là Local Traffic
                    self.ap_stats[bssid]["local_data"] += 1
                    if is_retry: self.ap_stats[bssid]["local_retry"] += 1
                    
                # Thu thập RSSI và Clients
                if hasattr(pkt, 'dBm_AntSignal'):
                    self.ap_stats[bssid]["rssi_list"].append(pkt.dBm_AntSignal)
                if src_mac and src_mac != GATEWAY_MAC and src_mac != bssid:
                    self.ap_stats[bssid]["clients"].add(src_mac)

    def _channel_hopper(self, channels=[36, 40, 44, 48, 1, 6, 11]):
        """Radar quét dải tần"""
        print(f"[*] Radar Channel Hopper kích hoạt trên {self.iface_mon}")
        while True:
            for ch in channels:
                os.system(f"iwconfig {self.iface_mon} channel {ch}")
                time.sleep(0.5) 

    def _parse_tshark_log(self):
        """(Tshark phần này giữ nguyên như trước)"""
        try:
            if not os.path.exists(self.raw_tshark_path): return 0.0, 0.0 
            with open(self.raw_tshark_path, 'r') as f:
                content = f.read()
                jitter_matches = re.findall(r'(\d+\.\d+)', content)
                jitter = float(jitter_matches[-1]) if jitter_matches else 0.0
                loss_matches = re.search(r'\((\d+\.\d+)%\)', content)
                loss_percent = float(loss_matches.group(1)) / 100.0 if loss_matches else 0.0
                return jitter, loss_percent
        except Exception:
            return 0.0, 0.0

    def etl_loop(self):
        """Vòng lặp ETL: Đóng gói bệnh án chi tiết cho từng AP"""
        print("[*] Per-AP & Traffic-Aware ETL Pipeline đang chạy...")
        while True:
            time.sleep(30) 
            
            with self.lock:
                now = datetime.now()
                is_break_time = 1 if (now.minute in range(45, 60)) else 0
                global_jitter, global_loss = self._parse_tshark_log()
                
                final_json_data = {}
                
                for bssid, stats in self.ap_stats.items():
                    total_pkts = stats["internet_data"] + stats["local_data"]
                    if total_pkts == 0: continue
                        
                    # Tính toán Tỷ lệ đụng độ chia theo Luồng
                    internet_p_c = (stats["internet_retry"] / stats["internet_data"]) if stats["internet_data"] > 0 else 0.0
                    local_p_c = (stats["local_retry"] / stats["local_data"]) if stats["local_data"] > 0 else 0.0
                    
                    # p_c tổng hợp cho AI (Ưu tiên trọng số Internet x3 lần Local)
                    # Nếu Internet nghẽn, AI sẽ báo động. Nếu Local nghẽn, AI châm chước.
                    blended_retry = (internet_p_c * 0.75) + (local_p_c * 0.25)
                    p_c = min(1.0, blended_retry + global_loss)
                    
                    avg_rssi = float(np.mean(stats["rssi_list"])) if stats["rssi_list"] else -90.0
                    sta_count = len(stats["clients"])
                    airtime_util = min(1.0, (total_pkts * 1500 * 8) / (50 * 1e6))
                    
                    final_json_data[bssid] = {
                        "avg_rssi": avg_rssi,
                        "p_c": p_c,
                        "retry_rate": blended_retry, # Lưu vào JSON để Dashboard hiển thị
                        "airtime_util": airtime_util,
                        "sta_count": sta_count,
                        "hour": now.hour,
                        "minute": now.minute,
                        "is_break_time": is_break_time,
                        "debug_info": f"Int_Drops:{internet_p_c:.2f} | Loc_Drops:{local_p_c:.2f}"
                    }
                
                # Xuất file JSON (Cho AI đọc Real-time)
                with open(self.processed_json_path, 'w') as f:
                    json.dump(final_json_data, f)
                
                # [THÊM MỚI] Ghi nối tiếp (Append) dữ liệu vào file CSV (Cho EDA vẽ biểu đồ)
                with open(self.history_csv_path, mode='a', newline='') as file:
                    writer = csv.writer(file)
                    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
                    
                    for bssid, data in final_json_data.items():
                        writer.writerow([
                            timestamp_str,          # Thời gian ghi nhận
                            data["hour"],           # Giờ
                            data["minute"],         # Phút
                            bssid,                  # Tên/MAC của AP
                            f"{data['avg_rssi']:.2f}", # Cường độ tín hiệu
                            f"{data['p_c']:.4f}",      # Xác suất va chạm
                            f"{data['retry_rate']:.4f}",# Tỷ lệ rớt gói
                            f"{data['airtime_util']:.4f}",# Tỷ lệ chiếm dụng Airtime
                            data["sta_count"]       # Số Client
                        ])
                
                # Dọn dẹp 'ngăn kéo' cho chu kỳ tiếp theo
                self.ap_stats.clear()

    def start(self):
        threading.Thread(target=self._channel_hopper, daemon=True).start()
        threading.Thread(target=lambda: sniff(iface=self.iface_mon, prn=self._scapy_handler, store=0), daemon=True).start()
        threading.Thread(target=self.etl_loop, daemon=True).start()

if __name__ == "__main__":
    node = MultiAPTelemetryETL(iface_mon="wlan0mon") 
    node.start()
    print("[+] Trạm Viễn Thông Nhận Thức Luồng (Traffic-Aware) đang chạy...")
    while True: time.sleep(1)