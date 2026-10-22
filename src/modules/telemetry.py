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
from scapy.layers.dot11 import Dot11, Dot11Beacon

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
        parts = section_text.split("===SURVEY===")
        station_raw = parts[0] if len(parts) > 0 else ""
        survey_raw = parts[1] if len(parts) > 1 else ""

        stations = re.findall(r"Station ([0-9a-fA-F:]{17})", station_raw)
        sta_count = len(stations)

        rssi_vals = [float(x) for x in re.findall(r"signal:\s*([-\d]+)\s*\[?", station_raw)]
        avg_rssi = float(np.mean(rssi_vals)) if rssi_vals else -90.0

        # Lấy cả TX (Downlink) và RX (Uplink - UDP Jammer/Video)
        tx_pkts = sum(int(x) for x in re.findall(r"tx packets:\s*(\d+)", station_raw))
        rx_pkts = sum(int(x) for x in re.findall(r"rx packets:\s*(\d+)", station_raw))
        tx_retries = sum(int(x) for x in re.findall(r"tx retries:\s*(\d+)", station_raw))
        tx_failed = sum(int(x) for x in re.findall(r"tx failed:\s*(\d+)", station_raw))
        total_pkts = tx_pkts + rx_pkts

        active_time, busy_time = 0, 0
        # Sửa Regex Survey để bắt khối "in use" an toàn hơn
        in_use_matches = re.finditer(r"frequency:\s*\d+\s*MHz\s*\[in use\].*?channel active time:\s*(\d+)\s*ms.*?channel busy time:\s*(\d+)\s*ms", survey_raw, re.DOTALL | re.IGNORECASE)
        for match in in_use_matches:
            act_t = int(match.group(1))
            bsy_t = int(match.group(2))
            if act_t > 0: # Lấy khối có thời gian active lớn nhất (đảm bảo đúng kênh đang phát)
                if act_t > active_time:
                    active_time = act_t
                    busy_time = bsy_t

        prev = self.prev_openwrt_counters.get(bssid)
        self.prev_openwrt_counters[bssid] = {
            "total_pkts": total_pkts,
            "tx_retries": tx_retries,
            "tx_failed": tx_failed,
            "active_time": active_time,
            "busy_time": busy_time,
        }

        if not prev:
            return None

        d_pkts = max(1, total_pkts - prev["total_pkts"])
        d_retries = max(0, tx_retries - prev["tx_retries"])
        d_failed = max(0, tx_failed - prev["tx_failed"])
        d_active = max(1, active_time - prev["active_time"])
        d_busy = max(0, busy_time - prev["busy_time"])

        retry_rate = float(np.clip(d_retries / (d_pkts + d_retries), 0.0, 1.0))
        # p_c phản ánh tình trạng rớt gói tổng thể (bao gồm cả va chạm và suy hao)
        p_c = float(np.clip((d_retries + d_failed * 2.0) / (d_pkts + d_retries + d_failed), 0.0, 1.0))
        airtime_util = float(np.clip(d_busy / d_active, 0.0, 1.0))

        return {
            "avg_rssi": avg_rssi,
            "p_c": p_c,
            "retry_rate": retry_rate,
            "airtime_util": airtime_util,
            "sta_count": sta_count,
            "band": band_label,
            "debug_info": f"[{band_label}] d_pkts:{d_pkts} | d_ret:{d_retries} | d_fail:{d_failed} | CCA:{airtime_util:.2f}"
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
                "iw dev phy0-ap0 station dump 2>/dev/null; "
                "echo '===SURVEY==='; "
                "iw dev phy0-ap0 survey dump 2>/dev/null; "
                "echo '===BAND_5G==='; "
                "iw dev phy1-ap0 station dump 2>/dev/null; "
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

    def _scapy_handler(self, pkt):
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
            with self.lock:
                if bssid not in self.ap_stats:
                    self.ap_stats[bssid] = {
                        "internet_data": 0, "internet_retry": 0,
                        "local_data": 0, "local_retry": 0,
                        "rssi_list": [], "clients": set()
                    }
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
        print("[*] Scapy Monitor-Mode ETL Pipeline đang chạy (TP-Link Archer T2U Plus)...")
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

                    final_json_data[bssid] = {
                        "avg_rssi": avg_rssi,
                        "p_c": p_c,
                        "retry_rate": blended_retry,
                        "airtime_util": airtime_util,
                        "sta_count": sta_count,
                        "hour": now.hour,
                        "minute": now.minute,
                        "is_break_time": is_break_time,
                        "zone": self.zone,
                        "debug_info": f"Int_Drops:{internet_p_c:.2f} | Loc_Drops:{local_p_c:.2f}"
                    }

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