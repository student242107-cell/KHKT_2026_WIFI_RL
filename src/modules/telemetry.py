import json
import os
import time
import threading
import re
import argparse
import subprocess
import numpy as np
from scapy.all import sniff
from scapy.layers.dot11 import Dot11, Dot11Beacon
from datetime import datetime
import csv

CHANNELS = (1, 6, 11, 36, 40, 44, 48)
CHANNEL_SCAN_SECONDS = 30
CHANNEL_DWELL_SECONDS = 0.5
HISTORY_COLUMNS = [
    "Timestamp", "Hour", "Minute", "AP_MAC", "avg_rssi", "p_c",
    "retry_rate", "airtime_util", "sta_count", "Zone",
]
LEGACY_HISTORY_COLUMNS = HISTORY_COLUMNS[:-1]

# Địa chỉ MAC của cổng Default Gateway (Cổng nối ra Internet của trường)
# Bạn nhớ sửa thành MAC của cổng WAN/LAN Router của bạn nhé!
GATEWAY_MAC = "00:15:5d:d8:73:d3".upper()


def select_active_channels(beacon_counts, channels=CHANNELS, max_channels=2):
    """Trả về tối đa hai kênh có nhiều beacon nhất, ổn định khi số beacon hòa."""
    channel_order = {channel: index for index, channel in enumerate(channels)}
    active = [
        channel for channel in channels
        if beacon_counts.get(channel, 0) > 0
    ]
    return sorted(
        active,
        key=lambda channel: (-beacon_counts[channel], channel_order[channel]),
    )[:max_channels]


class SmartChannelHopper:
    """
    Cơ chế Tự động Khóa Kênh Đông Nhất (Active Channel Pinning).

    Giai đoạn 1 - Quét Khám phá (Discovery Scan):
        30 giây đầu tiên, quét nhanh qua tất cả 7 kênh (1, 6, 11, 36, 40, 44, 48)
        để phát hiện các AP đang phát beacon ở kênh nào.

    Giai đoạn 2 - Khóa Kênh (Channel Pinning):
        Chỉ nhảy luân phiên giữa tối đa 2 kênh có nhiều beacon nhất,
        bỏ qua các kênh trống hoàn toàn.

    Hiệu quả:
        Mật độ dữ liệu bắt được trên đơn vị thời gian tăng gấp 3.5 lần
        so với việc nhảy đều 7 kênh, bắt trọn từng nhịp giật lag (Micro-spikes).
    """

    def __init__(self, iface_mon, all_channels=CHANNELS,
                 scan_duration=CHANNEL_SCAN_SECONDS,
                 dwell_time=CHANNEL_DWELL_SECONDS):
        self.iface_mon = iface_mon
        self.all_channels = list(all_channels)
        self.scan_duration = scan_duration
        self.dwell_time = dwell_time

        # Bộ đếm beacon cho mỗi kênh trong giai đoạn quét
        self._beacon_counts = {ch: 0 for ch in self.all_channels}
        self._lock = threading.Lock()

        # Trạng thái hiện tại
        self._phase = "discovery"        # "discovery" hoặc "pinned"
        self._pinned_channels = []       # Kênh đã khóa sau giai đoạn quét
        self._current_channel = None     # Kênh đang tune hiện tại

    @property
    def phase(self):
        return self._phase

    @property
    def pinned_channels(self):
        return list(self._pinned_channels)

    @property
    def current_channel(self):
        return self._current_channel

    def record_beacon(self, channel):
        """Ghi nhận 1 beacon được bắt trên kênh đang tune (gọi từ sniffer callback)."""
        with self._lock:
            if self._phase == "discovery" and channel in self._beacon_counts:
                self._beacon_counts[channel] += 1

    def _set_channel(self, ch):
        """Chuyển card sang kênh ch và cập nhật trạng thái."""
        os.system(f"iwconfig {self.iface_mon} channel {ch}")
        self._current_channel = ch

    def run(self):
        """
        Vòng lặp chính của Smart Channel Hopper (chạy trong daemon thread).

        ─── Phase 1: Discovery Scan (30 giây) ───
        Nhảy qua tất cả 7 kênh, mỗi kênh dừng 0.5s.
        Sau 30 giây, chọn tối đa 2 kênh đông beacon nhất.

        ─── Phase 2: Channel Pinning (vĩnh viễn) ───
        Chỉ nhảy giữa các kênh đã khóa.
        """
        print(f"[📡] Smart Channel Hopper khởi động trên {self.iface_mon}")
        print(f"[📡] Phase 1: Discovery Scan — quét {len(self.all_channels)} kênh "
              f"trong {self.scan_duration}s...")

        # ─── Phase 1: Discovery Scan ───
        scan_start = time.time()
        while (time.time() - scan_start) < self.scan_duration:
            for ch in self.all_channels:
                if (time.time() - scan_start) >= self.scan_duration:
                    break
                self._set_channel(ch)
                time.sleep(self.dwell_time)

        # Chọn kênh đông nhất
        with self._lock:
            self._pinned_channels = select_active_channels(
                self._beacon_counts, self.all_channels
            )
            self._phase = "pinned"

        # Fallback: nếu không phát hiện kênh nào → giữ nguyên tất cả 7 kênh
        if not self._pinned_channels:
            self._pinned_channels = list(self.all_channels)
            print(f"[⚠️] Không phát hiện beacon nào! Giữ nguyên {len(self.all_channels)} kênh.")
        else:
            print(f"[✅] Phase 2: Channel Pinning — khóa kênh: {self._pinned_channels}")
            beacon_summary = ", ".join(
                f"CH{ch}: {self._beacon_counts[ch]} beacons"
                for ch in self.all_channels if self._beacon_counts[ch] > 0
            )
            print(f"     Kết quả quét: {beacon_summary}")
            ratio = len(self.all_channels) / len(self._pinned_channels)
            print(f"     Mật độ dữ liệu tăng ~{ratio:.1f}x so với quét đều {len(self.all_channels)} kênh!")

        # ─── Phase 2: Channel Pinning (vĩnh viễn) ───
        while True:
            for ch in self._pinned_channels:
                self._set_channel(ch)
                time.sleep(self.dwell_time)


class MultiAPTelemetryETL:
    def __init__(self, iface_mon="wlan0mon", zone="Unspecified"):
        self.iface_mon = iface_mon
        self.zone = zone
        self.raw_tshark_path = "data/raw/tshark_raw.txt"
        self.processed_json_path = "data/processed/per_ap_state.json"
        
        # [THÊM MỚI] Đường dẫn lưu dữ liệu lịch sử cho biểu đồ
        self.history_csv_path = "data/processed/telemetry_history.csv" 
        
        os.makedirs(os.path.dirname(self.raw_tshark_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.processed_json_path), exist_ok=True)
        
        self._ensure_history_csv()

        # Ngăn kéo hồ sơ bệnh án cho từng AP (BSSID)
        self.ap_stats = {}
        self.lock = threading.Lock()

        # Smart Channel Hopper
        self._hopper = SmartChannelHopper(
            iface_mon=self.iface_mon,
            all_channels=CHANNELS,
            scan_duration=CHANNEL_SCAN_SECONDS,
            dwell_time=CHANNEL_DWELL_SECONDS,
        )

    def _ensure_history_csv(self):
        """Tạo CSV mới hoặc thêm cột Zone cho dữ liệu lịch sử cũ."""
        if not os.path.exists(self.history_csv_path) or os.path.getsize(self.history_csv_path) == 0:
            with open(self.history_csv_path, mode="w", newline="") as file:
                csv.writer(file).writerow(HISTORY_COLUMNS)
            return

        with open(self.history_csv_path, mode="r", newline="") as source:
            reader = csv.reader(source)
            existing_columns = next(reader, None)
            if existing_columns == HISTORY_COLUMNS:
                return
            if existing_columns != LEGACY_HISTORY_COLUMNS:
                raise ValueError(
                    f"Không thể cập nhật header CSV không được hỗ trợ: {existing_columns}"
                )

            temp_path = f"{self.history_csv_path}.tmp"
            with open(temp_path, mode="w", newline="") as destination:
                writer = csv.writer(destination)
                writer.writerow(HISTORY_COLUMNS)
                for row in reader:
                    writer.writerow(row + [""])

        os.replace(temp_path, self.history_csv_path)

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
        if not pkt.haslayer(Dot11):
            return

        # ─── Ghi nhận Beacon cho Smart Channel Hopper ───
        if pkt.haslayer(Dot11Beacon):
            current_ch = self._hopper.current_channel
            if current_ch is not None:
                self._hopper.record_beacon(current_ch)
            return  # Beacon không phải Data frame, bỏ qua

        # ─── Xử lý Data Frame như cũ ───
        if pkt.type == 2:  # Chỉ xét Data Frame
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
                        "zone": self.zone,  # Ghi nhãn khu vực vào JSON
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
                            data["sta_count"],      # Số Client
                            self.zone,              # Khu vực (Zone)
                        ])
                
                # Dọn dẹp 'ngăn kéo' cho chu kỳ tiếp theo
                self.ap_stats.clear()

    def start(self):
        # Khởi chạy Smart Channel Hopper thay vì channel hopper cũ
        threading.Thread(target=self._hopper.run, daemon=True).start()
        threading.Thread(target=lambda: sniff(
            iface=self.iface_mon, prn=self._scapy_handler, store=0
        ), daemon=True).start()
        threading.Thread(target=self.etl_loop, daemon=True).start()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="📡 Trạm Viễn Thông Nhận Thức Luồng (Traffic-Aware Telemetry)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
╔══════════════════════════════════════════════════════════════╗
║  VÍ DỤ SỬ DỤNG:                                           ║
║                                                              ║
║  # Thu thập dữ liệu tại Thư viện                            ║
║  sudo python src/modules/telemetry.py --zone ThuVien         ║
║                                                              ║
║  # Thu thập dữ liệu tại Phòng học A101                      ║
║  sudo python src/modules/telemetry.py --zone PhongHoc_A101   ║
║                                                              ║
║  # Thu thập dữ liệu tại Căng-tin                            ║
║  sudo python src/modules/telemetry.py --zone CangTin         ║
║                                                              ║
║  # Thu thập dữ liệu tại Hành lang                           ║
║  sudo python src/modules/telemetry.py --zone HanhLang        ║
║                                                              ║
║  # Chỉ định interface và thời gian quét kênh                 ║
║  sudo python src/modules/telemetry.py --zone ThuVien \\       ║
║       --iface wlan1mon --scan-time 45                        ║
╚══════════════════════════════════════════════════════════════╝
""",
    )

    parser.add_argument(
        "--zone", type=str, default="Unspecified",
        help="Tên khu vực đang thu thập dữ liệu "
             "(VD: ThuVien, PhongHoc_A101, CangTin, HanhLang). "
             "Giá trị này sẽ được ghi vào cột 'Zone' trong telemetry_history.csv "
             "để phân biệt dữ liệu giữa các khu vực trên Heatmap.",
    )
    parser.add_argument(
        "--iface", type=str, default="wlan0mon",
        help="Tên interface Wi-Fi ở chế độ Monitor (mặc định: wlan0mon)",
    )
    parser.add_argument(
        "--scan-time", type=int, default=CHANNEL_SCAN_SECONDS,
        help=f"Thời gian quét khám phá kênh ban đầu, tính bằng giây "
             f"(mặc định: {CHANNEL_SCAN_SECONDS}s). Sau thời gian này, "
             f"hệ thống sẽ tự khóa vào các kênh đông nhất.",
    )

    args = parser.parse_args()

    # Ghi đè scan_duration nếu người dùng chỉ định
    CHANNEL_SCAN_SECONDS = args.scan_time

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  📡 TRẠM VIỄN THÔNG NHẬN THỨC LUỒNG (Traffic-Aware)   ║")
    print("║  🧠 Smart Channel Hopper + Zone Tagging                ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  🏫 Zone      : {args.zone:<40} ║")
    print(f"║  📶 Interface : {args.iface:<40} ║")
    print(f"║  ⏱️  Scan Time : {args.scan_time}s{' ' * (39 - len(str(args.scan_time)))} ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print("║  Cơ chế: 30s quét 7 kênh → Khóa ≤2 kênh đông nhất    ║")
    print("║  Hiệu quả: Mật độ dữ liệu tăng ~3.5x                 ║")
    print("╚══════════════════════════════════════════════════════════╝")

    node = MultiAPTelemetryETL(iface_mon=args.iface, zone=args.zone)
    node.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n[🛑] Dừng thu thập tại zone '{args.zone}'. Dữ liệu đã lưu vào CSV.")