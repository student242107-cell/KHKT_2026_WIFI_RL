import json
import os
import time
import threading
import re
import argparse
import numpy as np
from datetime import datetime
import csv
import yaml
import paramiko
from dotenv import load_dotenv

from scapy.all import sniff
from scapy.layers.dot11 import Dot11, Dot11Beacon, RadioTap

load_dotenv()

CHANNELS = (1, 6, 11, 36, 40, 44, 48)
CHANNEL_SCAN_SECONDS = 30
CHANNEL_DWELL_SECONDS = 0.5
HISTORY_COLUMNS = [
    "Timestamp", "Hour", "Minute", "AP_MAC", "avg_rssi", "p_c",
    "retry_rate", "airtime_util", "sta_count", "Zone",
]
LEGACY_HISTORY_COLUMNS = HISTORY_COLUMNS[:-1]

GATEWAY_MAC = "00:15:5d:d8:73:d3".upper()
CONFIG_PATH = "configs/network_config.yaml"


def select_active_channels(beacon_counts, channels=CHANNELS, max_channels=2):
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
    def __init__(self, iface_mon, all_channels=CHANNELS,
                 scan_duration=CHANNEL_SCAN_SECONDS,
                 dwell_time=CHANNEL_DWELL_SECONDS):
        self.iface_mon = iface_mon
        self.all_channels = list(all_channels)
        self.scan_duration = scan_duration
        self.dwell_time = dwell_time
        self._beacon_counts = {ch: 0 for ch in self.all_channels}
        self._lock = threading.Lock()
        self._phase = "discovery"
        self._pinned_channels = []
        self._current_channel = None

    @property
    def current_channel(self):
        return self._current_channel

    def record_beacon(self, channel):
        with self._lock:
            if self._phase == "discovery" and channel in self._beacon_counts:
                self._beacon_counts[channel] += 1

    def _set_channel(self, ch):
        os.system(f"iwconfig {self.iface_mon} channel {ch}")
        self._current_channel = ch

    def run(self):
        print(f"[📡] Smart Channel Hopper khởi động trên {self.iface_mon}")
        scan_start = time.time()
        while (time.time() - scan_start) < self.scan_duration:
            for ch in self.all_channels:
                if (time.time() - scan_start) >= self.scan_duration:
                    break
                self._set_channel(ch)
                time.sleep(self.dwell_time)

        with self._lock:
            self._pinned_channels = select_active_channels(
                self._beacon_counts, self.all_channels
            )
            self._phase = "pinned"

        if not self._pinned_channels:
            self._pinned_channels = list(self.all_channels)
            print(f"[⚠️] Không phát hiện beacon! Giữ nguyên {len(self.all_channels)} kênh.")
        else:
            print(f"[✅] Phase 2: Channel Pinning — khóa kênh: {self._pinned_channels}")

        while True:
            for ch in self._pinned_channels:
                self._set_channel(ch)
                time.sleep(self.dwell_time)


class MultiAPTelemetryETL:
    def __init__(self, mode="openwrt", iface_mon="wlan0mon", zone="Unspecified", poll_interval=3):
        self.mode = mode
        self.iface_mon = iface_mon
        self.zone = zone
        self.poll_interval = poll_interval

        self.raw_tshark_path = "data/raw/tshark_raw.txt"
        self.processed_json_path = "data/processed/per_ap_state.json"
        self.history_csv_path = "data/processed/telemetry_history.csv"

        os.makedirs(os.path.dirname(self.raw_tshark_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.processed_json_path), exist_ok=True)
        self._ensure_history_csv()

        self.ap_stats = {}
        self.lock = threading.Lock()

        self.router_user = os.getenv("ROUTER_USER", "root")
        self.router_pass = os.getenv("ROUTER_PASS", "")
        self.nodes = self._load_nodes_from_yaml()
        self.prev_openwrt_counters = {}

        if self.mode == "scapy":
            self._hopper = SmartChannelHopper(
                iface_mon=self.iface_mon,
                all_channels=CHANNELS,
                scan_duration=CHANNEL_SCAN_SECONDS,
                dwell_time=CHANNEL_DWELL_SECONDS,
            )

    def _load_nodes_from_yaml(self):
        if not os.path.exists(CONFIG_PATH):
            return []
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f)
                topo = cfg.get("network_topology", {})
                nodes = topo.get("nodes", [])
                if nodes:
                    return nodes

                ip_dict = {}
                for bssid, ip in topo.get("ap_map", {}).items():
                    ip_dict.setdefault(ip, {"name": f"AP_{ip}", "ip": ip})["bssid_5g"] = bssid.upper()
                for bssid, ip in topo.get("ap_map_2g", {}).items():
                    ip_dict.setdefault(ip, {"name": f"AP_{ip}", "ip": ip})["bssid_2g"] = bssid.upper()
                return list(ip_dict.values())
        except Exception as e:
            print(f"[!] Lỗi đọc {CONFIG_PATH}: {e}")
            return []

    def _ensure_history_csv(self):
        if not os.path.exists(self.history_csv_path) or os.path.getsize(self.history_csv_path) == 0:
            with open(self.history_csv_path, mode="w", newline="") as file:
                csv.writer(file).writerow(HISTORY_COLUMNS)
            return

        with open(self.history_csv_path, mode="r", newline="") as source:
            reader = csv.reader(source)
            existing_columns = next(reader, None)
            if existing_columns == HISTORY_COLUMNS:
                return
            if existing_columns == LEGACY_HISTORY_COLUMNS:
                temp_path = f"{self.history_csv_path}.tmp"
                with open(temp_path, mode="w", newline="") as destination:
                    writer = csv.writer(destination)
                    writer.writerow(HISTORY_COLUMNS)
                    for row in reader:
                        writer.writerow(row + [""])
                os.replace(temp_path, self.history_csv_path)

    def _save_outputs(self, final_json_data, now):
        if not final_json_data:
            return

        with open(self.processed_json_path, "w") as f:
            json.dump(final_json_data, f, indent=2)

        with open(self.history_csv_path, mode="a", newline="") as file:
            writer = csv.writer(file)
            timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
            for bssid, data in final_json_data.items():
                writer.writerow([
                    timestamp_str,
                    data["hour"],
                    data["minute"],
                    bssid,
                    f"{data['avg_rssi']:.2f}",
                    f"{data['p_c']:.4f}",
                    f"{data['retry_rate']:.4f}",
                    f"{data['airtime_util']:.4f}",
                    data["sta_count"],
                    self.zone,
                ])

    def _parse_iface_section(self, bssid, band_label, section_text):
        """Bóc tách station dump & survey dump chuẩn xác cho chip MediaTek mt76."""
        parts = section_text.split("===SURVEY===")
        station_raw = parts[0] if len(parts) > 0 else ""
        survey_raw = parts[1] if len(parts) > 1 else ""

        stations = re.findall(r"Station ([0-9a-fA-F:]{17})", station_raw)
        sta_count = len(stations)

        rssi_vals = [float(x) for x in re.findall(r"signal:\s*([-\d]+)\s*\[?", station_raw)]
        avg_rssi = float(np.mean(rssi_vals)) if rssi_vals else -90.0

        tx_pkts = sum(int(x) for x in re.findall(r"tx packets:\s*(\d+)", station_raw))
        rx_pkts = sum(int(x) for x in re.findall(r"rx packets:\s*(\d+)", station_raw))
        tx_retries = sum(int(x) for x in re.findall(r"tx retries:\s*(\d+)", station_raw))
        tx_failed = sum(int(x) for x in re.findall(r"tx failed:\s*(\d+)", station_raw))

        # Tách riêng từng khối Survey data của từng kênh để không bị nhảy dòng Regex
        active_time, busy_time = 0, 0
        noise_floor = -95.0
        survey_blocks = survey_raw.split("Survey data from")
        for blk in survey_blocks:
            if "[in use]" in blk:
                act_m = re.search(r"channel active time:\s*(\d+)", blk)
                bsy_m = re.search(r"channel busy time:\s*(\d+)", blk)
                rx_m = re.search(r"channel receive time:\s*(\d+)", blk)
                tx_m = re.search(r"channel transmit time:\s*(\d+)", blk)
                noise_m = re.search(r"noise:\s*([-\d]+)", blk)
                if noise_m:
                    noise_floor = float(noise_m.group(1))
                if act_m:
                    active_time = int(act_m.group(1))
                    raw_busy = int(bsy_m.group(1)) if bsy_m else 0
                    raw_rx = int(rx_m.group(1)) if rx_m else 0
                    raw_tx = int(tx_m.group(1)) if tx_m else 0
                    busy_time = max(raw_busy, raw_rx + raw_tx)
                    break

        prev = self.prev_openwrt_counters.get(bssid)
        self.prev_openwrt_counters[bssid] = {
            "tx_pkts": tx_pkts,
            "rx_pkts": rx_pkts,
            "tx_retries": tx_retries,
            "tx_failed": tx_failed,
            "active_time": active_time,
            "busy_time": busy_time,
        }

        if not prev:
            return None

        d_tx = max(1, tx_pkts - prev["tx_pkts"])
        d_rx = max(0, rx_pkts - prev["rx_pkts"])
        d_retries = max(0, tx_retries - prev["tx_retries"])
        d_failed = max(0, tx_failed - prev["tx_failed"])

        # Nếu vừa đổi kênh làm bộ đếm active_time của kênh mới nhỏ hơn kênh cũ
        if active_time >= prev["active_time"]:
            d_active = max(1, active_time - prev["active_time"])
            d_busy = max(0, busy_time - prev["busy_time"])
        else:
            d_active = max(1, active_time)
            d_busy = max(0, busy_time)

        cca_airtime = float(np.clip(d_busy / d_active, 0.0, 1.0))

        # Tính mật độ tải Uplink + Downlink (số gói/giây) để bắt chính xác bão UDP Jammer
        pps = (d_tx + d_rx) / max(1.0, float(self.poll_interval))
        traffic_airtime = float(np.clip(pps / 7500.0, 0.0, 0.98))
        airtime_util = max(cca_airtime, traffic_airtime)

        # Tỷ lệ retry tính đúng trên số gói TX (không chia cho d_rx để tránh bị pha loãng về 0.001)
        tx_retry_ratio = float(np.clip(d_retries / (d_tx + d_retries), 0.0, 1.0)) if d_tx > 20 else 0.0
        tx_fail_ratio = float(np.clip((d_retries + d_failed * 2.0) / (d_tx + d_retries + d_failed), 0.0, 1.0)) if d_tx > 20 else 0.0

        # Khi có bão UDP (pps > 2500 gói/s hoặc airtime > 65%), phản ánh trực tiếp vào p_c và retry_rate
        congestion_pc = float(np.clip((airtime_util - 0.45) * 1.4, 0.0, 0.95)) if (sta_count > 0 and airtime_util > 0.50) else 0.0
        p_c = float(np.clip(max(tx_fail_ratio, congestion_pc), 0.0, 1.0))
        retry_rate = float(np.clip(max(tx_retry_ratio, p_c * 0.75), 0.0, 1.0))

        return {
            "avg_rssi": avg_rssi,
            "noise_floor": noise_floor,
            "p_c": p_c,
            "retry_rate": retry_rate,
            "airtime_util": airtime_util,
            "sta_count": sta_count,
            "ofdma_ratio": 0.5,
            "band": band_label,
            "debug_info": f"[{band_label}] PPS:{pps:.0f} | d_tx:{d_tx} | d_ret:{d_retries} | CCA:{airtime_util:.2f} | NF:{noise_floor:.0f}dBm"
        }

    def _poll_openwrt_node_dualband(self, node):
        ip = node["ip"]
        bssid_2g = node.get("bssid_2g")
        bssid_5g = node.get("bssid_5g")

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(ip, username=self.router_user, password=self.router_pass, timeout=3)
            cmd = (
                "echo '===BAND_2G==='; "
                "for dev in /sys/class/net/phy0-ap*; do "
                "  [ -e \"$dev\" ] && iw dev $(basename $dev) station dump 2>/dev/null; "
                "done; "
                "echo '===SURVEY==='; "
                "iw dev phy0-ap0 survey dump 2>/dev/null; "
                "echo '===BAND_5G==='; "
                "for dev in /sys/class/net/phy1-ap*; do "
                "  [ -e \"$dev\" ] && iw dev $(basename $dev) station dump 2>/dev/null; "
                "done; "
                "echo '===SURVEY==='; "
                "iw dev phy1-ap0 survey dump 2>/dev/null"
            )
            _, stdout, _ = ssh.exec_command(cmd)
            raw_out = stdout.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[!] Lỗi SSH tới {node.get('name', ip)} ({ip}): {e}")
            return {}
        finally:
            ssh.close()

        results = {}
        if "===BAND_5G===" in raw_out:
            part_2g, part_5g = raw_out.split("===BAND_5G===", 1)
            part_2g = part_2g.replace("===BAND_2G===", "")

            if bssid_2g:
                m2 = self._parse_iface_section(bssid_2g.upper(), "2.4G", part_2g)
                if m2:
                    results[bssid_2g.upper()] = m2
            if bssid_5g:
                m5 = self._parse_iface_section(bssid_5g.upper(), "5G", part_5g)
                if m5:
                    results[bssid_5g.upper()] = m5

        return results

    def openwrt_polling_loop(self):
        print(
            f"[*] OpenWrt Dual-Band Telemetry (2.4GHz + 5GHz) đang chạy "
            f"cho {len(self.nodes)} trạm phát (Chu kỳ: {self.poll_interval}s, Zone: {self.zone})..."
        )
        while True:
            now = datetime.now()
            is_break_time = 1 if (now.minute in range(45, 60)) else 0
            final_json_data = {}

            for node in self.nodes:
                node_metrics = self._poll_openwrt_node_dualband(node)
                for bssid, metrics in node_metrics.items():
                    metrics.update({
                        "hour": now.hour,
                        "minute": now.minute,
                        "is_break_time": is_break_time,
                        "zone": self.zone,
                    })
                    final_json_data[bssid] = metrics
                    print(
                        f"[📊 {metrics['band']:4}] {node.get('name', node['ip']):13} ({bssid}) | "
                        f"STAs: {metrics['sta_count']} | RSSI: {metrics['avg_rssi']:.1f}dBm | "
                        f"p_c: {metrics['p_c']:.3f} | Retry: {metrics['retry_rate']:.3f} | "
                        f"Airtime: {metrics['airtime_util']:.2%}"
                    )

            self._save_outputs(final_json_data, now)
            time.sleep(self.poll_interval)

    def _parse_mac_frame(self, pkt):
        dot11 = pkt.getlayer(Dot11)
        fc_field = int(dot11.FCfield)
        to_ds = bool(fc_field & 0x1)
        from_ds = bool(fc_field & 0x2)
        src_addr, dst_addr, bssid = None, None, None

        if not to_ds and not from_ds:
            dst_addr, src_addr, bssid = dot11.addr1, dot11.addr2, dot11.addr3
        elif to_ds and not from_ds:
            bssid, src_addr, dst_addr = dot11.addr1, dot11.addr2, dot11.addr3
        elif not to_ds and from_ds:
            dst_addr, bssid, src_addr = dot11.addr1, dot11.addr2, dot11.addr3
        elif to_ds and from_ds:
            dst_addr, src_addr = dot11.addr3, getattr(dot11, 'addr4', None)
            bssid = dot11.addr1

        return (
            bssid.upper() if bssid else None,
            src_addr.upper() if src_addr else None,
            dst_addr.upper() if dst_addr else None,
        )

    def _classify_phy_preamble(self, pkt):
        """
        PHY Preamble Sensing — Phân loại khung tin Wi-Fi qua tiêu đề tầng Vật lý.

        Ứng dụng nguyên lý từ US Patent 10,637,706 B2 (Packet Format Detection):
        Thay vì giải mã toàn bộ Payload hoặc tính ma trận CSI nặng nề, chỉ đọc
        các cờ hiệu (flags) trong Radiotap header mà card Wi-Fi Monitor Mode cung
        cấp sẵn. Kernel Linux + mac80211 tự động decode cờ RL-SIG/HE-SIG-A/VHT-SIG
        từ 4µs Preamble và trình bày trong Radiotap present bitmask.

        Phân loại PHY Format:
        - 'HE_MU' : Wi-Fi 6 Multi-User OFDMA (Radiotap bit 24 HE_MU bật)
        - 'HE_SU' : Wi-Fi 6 Single-User     (Radiotap bit 23 HE bật, không có HE_MU)
        - 'VHT'   : Wi-Fi 5 (802.11ac)       (Radiotap bit 21 VHT bật)
        - 'HT'    : Wi-Fi 4 (802.11n)        (Radiotap bit 19 MCS bật)
        - 'LEGACY': Wi-Fi cũ (802.11a/b/g)   (Chỉ có Rate field, không HT/VHT/HE)

        Ngoài ra trích xuất: noise_floor (dBm), L-SIG rate (Mbps)

        Returns:
            dict: {
                'phy_format': str,       # 'HE_MU', 'HE_SU', 'VHT', 'HT', 'LEGACY'
                'has_rlsig': bool,       # True nếu Radiotap bit L_SIG (27) bật
                'noise_floor': float,    # dBm_AntNoise hoặc None
                'lsig_rate': int|None,   # L-SIG data rate (x0.5 Mbps) hoặc None
            }
        """
        result = {
            'phy_format': 'LEGACY',
            'has_rlsig': False,
            'noise_floor': None,
            'lsig_rate': None,
        }

        if not pkt.haslayer(RadioTap):
            return result

        rt = pkt.getlayer(RadioTap)
        present = rt.present if hasattr(rt, 'present') and rt.present else 0

        # === Radiotap present bitmask (IEEE 802.11 Radiotap Capture Header) ===
        # Bit 19: MCS         → khung HT (Wi-Fi 4 / 802.11n)
        # Bit 21: VHT         → khung VHT (Wi-Fi 5 / 802.11ac)
        # Bit 23: HE          → khung HE (Wi-Fi 6 / 802.11ax) — detect qua RL-SIG
        # Bit 24: HE_MU       → khung HE Multi-User (OFDMA)
        # Bit 27: L_SIG       → trường L-SIG/RL-SIG có mặt (US Patent 10,637,706 B2)

        BIT_MCS    = 1 << 19   # HT (Wi-Fi 4)
        BIT_VHT    = 1 << 21   # VHT (Wi-Fi 5)
        BIT_HE     = 1 << 23   # HE (Wi-Fi 6) — cờ RL-SIG đã được kernel decode
        BIT_HE_MU  = 1 << 24   # HE MU-OFDMA
        BIT_L_SIG  = 1 << 27   # L-SIG field present (RL-SIG indicator)

        # --- Detect RL-SIG (Repeated L-SIG) ---
        # Theo US Patent 10,637,706 B2: sự xuất hiện cờ RL-SIG (lặp lại L-SIG bằng
        # điều chế BPSK) là dấu hiệu quyết định phân biệt khung Wi-Fi 6 (HE) với
        # khung Legacy/HT/VHT. Kernel mac80211 tự động detect RL-SIG và set bit 27.
        if present & BIT_L_SIG:
            result['has_rlsig'] = True
            # Trích xuất L-SIG Rate nếu có
            if hasattr(rt, 'lsig_rate') and rt.lsig_rate is not None:
                result['lsig_rate'] = int(rt.lsig_rate)

        # --- Phân loại PHY Format theo thứ tự ưu tiên ---
        if present & BIT_HE_MU:
            # HE MU-OFDMA: Router đang phục vụ nhiều client đồng thời trên các
            # Resource Unit (RU 26/52/106/242-tone) song song
            result['phy_format'] = 'HE_MU'
        elif present & BIT_HE:
            # HE Single-User: Khung Wi-Fi 6 nhưng chỉ phục vụ 1 client tại 1 thời điểm
            result['phy_format'] = 'HE_SU'
        elif present & BIT_VHT:
            result['phy_format'] = 'VHT'
        elif present & BIT_MCS:
            result['phy_format'] = 'HT'
        # else: LEGACY (mặc định)

        # --- Trích xuất Noise Floor ---
        if hasattr(rt, 'dBm_AntNoise') and rt.dBm_AntNoise is not None:
            noise = int(rt.dBm_AntNoise)
            # Lọc giá trị bất thường (driver trả 0 hoặc giá trị dương vô nghĩa)
            if -120 <= noise <= -20:
                result['noise_floor'] = float(noise)

        return result

    def _scapy_handler(self, pkt):
        """
        Handler Dual-Sensing: Kết hợp PHY Preamble Sensing + MAC-Layer Sensing.

        Tầng PHY (US Patent 10,637,706 B2):
          - Đọc cờ RL-SIG qua Radiotap bit 27 (L_SIG) để xác nhận khung Wi-Fi 6
          - Phân loại HE_MU (OFDMA) vs HE_SU vs VHT vs HT vs LEGACY
          - Trích xuất Noise Floor (dBm_AntNoise)

        Tầng MAC:
          - Đếm Data Frame, Retry Frame, ACK Frame
          - Phân luồng Internet vs Local traffic
          - Đo RSSI (dBm_AntSignal)
        """
        if not pkt.haslayer(Dot11):
            return
        if pkt.haslayer(Dot11Beacon):
            current_ch = self._hopper.current_channel
            if current_ch is not None:
                self._hopper.record_beacon(current_ch)
            return
        if pkt.type == 2:
            bssid, src_mac, dst_mac = self._parse_mac_frame(pkt)
            if not bssid or bssid == "FF:FF:FF:FF:FF:FF":
                return

            # === PHY Preamble Sensing (chỉ đọc 4µs Preamble, O(1), < 1MB RAM) ===
            phy_info = self._classify_phy_preamble(pkt)

            with self.lock:
                if bssid not in self.ap_stats:
                    self.ap_stats[bssid] = {
                        "internet_data": 0, "internet_retry": 0,
                        "local_data": 0, "local_retry": 0,
                        "rssi_list": [], "clients": set(),
                        # PHY Preamble Sensing counters
                        "he_su_frames": 0,
                        "he_mu_frames": 0,
                        "vht_frames": 0,
                        "ht_frames": 0,
                        "legacy_frames": 0,
                        "rlsig_detected": 0,
                        "noise_floor_samples": [],
                    }

                # --- MAC-Layer Sensing ---
                is_retry = (pkt.FCfield & 0x08) != 0
                if src_mac == GATEWAY_MAC or dst_mac == GATEWAY_MAC:
                    self.ap_stats[bssid]["internet_data"] += 1
                    if is_retry:
                        self.ap_stats[bssid]["internet_retry"] += 1
                else:
                    self.ap_stats[bssid]["local_data"] += 1
                    if is_retry:
                        self.ap_stats[bssid]["local_retry"] += 1
                if hasattr(pkt, 'dBm_AntSignal'):
                    self.ap_stats[bssid]["rssi_list"].append(pkt.dBm_AntSignal)
                if src_mac and src_mac != GATEWAY_MAC and src_mac != bssid:
                    self.ap_stats[bssid]["clients"].add(src_mac)

                # --- PHY Preamble Sensing counters ---
                fmt = phy_info['phy_format']
                if fmt == 'HE_MU':
                    self.ap_stats[bssid]["he_mu_frames"] += 1
                elif fmt == 'HE_SU':
                    self.ap_stats[bssid]["he_su_frames"] += 1
                elif fmt == 'VHT':
                    self.ap_stats[bssid]["vht_frames"] += 1
                elif fmt == 'HT':
                    self.ap_stats[bssid]["ht_frames"] += 1
                else:
                    self.ap_stats[bssid]["legacy_frames"] += 1

                if phy_info['has_rlsig']:
                    self.ap_stats[bssid]["rlsig_detected"] += 1

                if phy_info['noise_floor'] is not None:
                    self.ap_stats[bssid]["noise_floor_samples"].append(
                        phy_info['noise_floor']
                    )

    def _parse_tshark_log(self):
        try:
            if not os.path.exists(self.raw_tshark_path):
                return 0.0, 0.0
            with open(self.raw_tshark_path, 'r') as f:
                content = f.read()
                jitter_matches = re.findall(r'(\d+\.\d+)', content)
                jitter = float(jitter_matches[-1]) if jitter_matches else 0.0
                loss_matches = re.search(r'\((\d+\.\d+)%\)', content)
                loss_percent = float(loss_matches.group(1)) / 100.0 if loss_matches else 0.0
                return jitter, loss_percent
        except Exception:
            return 0.0, 0.0

    def scapy_etl_loop(self):
        """
        Chu kỳ ETL tổng hợp dữ liệu Dual-Sensing (PHY Preamble + MAC Layer).
        Mỗi 30 giây: tổng hợp, tính toán OFDMA Ratio, ghi output, reset bộ đếm.
        """
        print("[*] Scapy Dual-Sensing ETL Pipeline đang chạy (PHY Preamble + MAC Layer)...")
        while True:
            time.sleep(30)
            with self.lock:
                now = datetime.now()
                is_break_time = 1 if (now.minute in range(45, 60)) else 0
                _, global_loss = self._parse_tshark_log()
                final_json_data = {}

                for bssid, stats in self.ap_stats.items():
                    total_pkts = stats["internet_data"] + stats["local_data"]
                    if total_pkts == 0:
                        continue
                    internet_p_c = (stats["internet_retry"] / stats["internet_data"]) if stats["internet_data"] > 0 else 0.0
                    local_p_c = (stats["local_retry"] / stats["local_data"]) if stats["local_data"] > 0 else 0.0
                    blended_retry = (internet_p_c * 0.75) + (local_p_c * 0.25)
                    p_c = min(1.0, blended_retry + global_loss)
                    avg_rssi = float(np.mean(stats["rssi_list"])) if stats["rssi_list"] else -90.0
                    sta_count = len(stats["clients"])
                    airtime_util = min(1.0, (total_pkts * 1500 * 8) / (50 * 1e6))

                    # === PHY Preamble Sensing Metrics ===
                    he_su = stats.get("he_su_frames", 0)
                    he_mu = stats.get("he_mu_frames", 0)
                    vht = stats.get("vht_frames", 0)
                    ht = stats.get("ht_frames", 0)
                    legacy = stats.get("legacy_frames", 0)
                    rlsig = stats.get("rlsig_detected", 0)
                    he_total = he_su + he_mu

                    # OFDMA Ratio: Tỷ lệ khung HE-MU (OFDMA) trên tổng khung Wi-Fi 6
                    # Giá trị cao → Router đang dùng đa truy cập OFDMA hiệu quả
                    ofdma_ratio = (he_mu / he_total) if he_total > 0 else 0.0

                    # WiFi 6 Adoption: Tỷ lệ khung HE trên tổng toàn bộ
                    he_adoption = (he_total / total_pkts) if total_pkts > 0 else 0.0

                    # Noise Floor trung bình
                    nf_samples = stats.get("noise_floor_samples", [])
                    noise_floor = float(np.mean(nf_samples)) if nf_samples else None

                    final_json_data[bssid] = {
                        "avg_rssi": avg_rssi,
                        "noise_floor": round(noise_floor, 1) if noise_floor is not None else -92.0,
                        "p_c": p_c,
                        "retry_rate": blended_retry,
                        "airtime_util": airtime_util,
                        "sta_count": sta_count,
                        "ofdma_ratio": round(ofdma_ratio, 4),
                        "hour": now.hour,
                        "minute": now.minute,
                        "is_break_time": is_break_time,
                        "zone": self.zone,
                        # PHY Preamble Sensing output
                        "phy_sensing": {
                            "he_su_frames": he_su,
                            "he_mu_frames": he_mu,
                            "vht_frames": vht,
                            "ht_frames": ht,
                            "legacy_frames": legacy,
                            "rlsig_detected": rlsig,
                            "ofdma_ratio": round(ofdma_ratio, 4),
                            "he_adoption": round(he_adoption, 4),
                            "noise_floor_dbm": round(noise_floor, 1) if noise_floor else None,
                        },
                        "debug_info": (
                            f"Int_Drops:{internet_p_c:.2f} | Loc_Drops:{local_p_c:.2f} | "
                            f"PHY[HE_SU:{he_su} HE_MU:{he_mu} VHT:{vht} HT:{ht} LEG:{legacy}] "
                            f"OFDMA:{ofdma_ratio:.1%} RL-SIG:{rlsig}"
                        )
                    }

                    # Log PHY Preamble Sensing kết quả
                    nf_str = f"{noise_floor:.0f}dBm" if noise_floor else "N/A"
                    print(
                        f"[📡 PHY+MAC] {bssid} | "
                        f"HE_SU:{he_su} HE_MU:{he_mu} VHT:{vht} HT:{ht} LEG:{legacy} | "
                        f"OFDMA:{ofdma_ratio:.1%} HE%:{he_adoption:.1%} | "
                        f"RL-SIG:{rlsig} NF:{nf_str} | "
                        f"RSSI:{avg_rssi:.1f}dBm p_c:{p_c:.3f} STAs:{sta_count}"
                    )

                self._save_outputs(final_json_data, now)
                self.ap_stats.clear()

    def start(self):
        if self.mode == "openwrt":
            threading.Thread(target=self.openwrt_polling_loop, daemon=True).start()
        else:
            threading.Thread(target=self._hopper.run, daemon=True).start()
            threading.Thread(target=lambda: sniff(
                iface=self.iface_mon, prn=self._scapy_handler, store=0
            ), daemon=True).start()
            threading.Thread(target=self.scapy_etl_loop, daemon=True).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="📡 Dual-Mode & Dual-Band Cognitive Wi-Fi Telemetry Engine")
    parser.add_argument(
        "--mode", type=str, choices=["openwrt", "scapy"], default="openwrt",
        help="Chế độ thu thập: 'openwrt' (Quét cả 2.4GHz & 5GHz trên mọi AP trong YAML) hoặc 'scapy' (Monitor Mode)"
    )
    parser.add_argument("--zone", type=str, default="KHKT_Testbed", help="Tên khu vực (VD: CangTin, PhongHoc_A101, KHKT_Testbed)")
    parser.add_argument("--iface", type=str, default="wlan0mon", help="Interface Monitor cho mode scapy")
    parser.add_argument("--interval", type=int, default=3, help="Chu kỳ lấy mẫu (giây) cho mode openwrt")
    parser.add_argument("--scan-time", type=int, default=CHANNEL_SCAN_SECONDS, help="Thời gian quét kênh mode scapy")

    args = parser.parse_args()
    CHANNEL_SCAN_SECONDS = args.scan_time

    node = MultiAPTelemetryETL(
        mode=args.mode,
        iface_mon=args.iface,
        zone=args.zone,
        poll_interval=args.interval
    )
    node.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n[🛑] Đã dừng Telemetry Engine (mode={args.mode}, zone={args.zone}).")