"""
QoE Recorder — Ghi nhận Latency và Packet Loss khi HIL Agent hoạt động.

Module này chạy như một daemon thread nền. Khi deploy_hil_agent.py khởi động,
nó sẽ gọi qoe_recorder.start() để bắt đầu đo latency (ping) và ghi packet loss
liên tục vào file CSV. File CSV này là nguồn dữ liệu cho visualize_qoe.py.

Cách hoạt động:
  - Mỗi chu kỳ (mặc định 3 giây), gửi 10 gói ICMP ping tới gateway
  - Tính P50, P95, P99 latency và tỷ lệ packet loss từ kết quả ping
  - Ghi vào data/processed/qoe_metrics.csv

File CSV có cấu trúc:
  Timestamp, latency_min, latency_avg, latency_max, latency_p50, latency_p95,
  latency_p99, jitter, packet_loss_pct
"""

import os
import csv
import time
import subprocess
import re
import threading
import logging
import numpy as np
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

QOE_CSV_PATH = "data/processed/qoe_metrics.csv"
QOE_CSV_HEADERS = [
    "Timestamp",
    "latency_min_ms",
    "latency_avg_ms",
    "latency_max_ms",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "jitter_ms",
    "packet_loss_pct",
]

# Trạng thái toàn cục để kiểm tra recorder đã chạy chưa
_recorder_started = False
_recorder_lock = threading.Lock()


def _ensure_csv():
    """Tạo file CSV nếu chưa tồn tại."""
    os.makedirs(os.path.dirname(QOE_CSV_PATH), exist_ok=True)
    if not os.path.exists(QOE_CSV_PATH) or os.path.getsize(QOE_CSV_PATH) == 0:
        with open(QOE_CSV_PATH, mode="w", newline="") as f:
            csv.writer(f).writerow(QOE_CSV_HEADERS)


def _ping_gateway(gateway_ip, count=10, timeout=2):
    """
    Gửi `count` gói ICMP ping và trả về danh sách RTT (ms) + tỷ lệ mất gói.

    Returns:
        (list[float], float): (danh sách RTT ms, packet_loss_percent 0-100)
    """
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), "-i", "0.2", gateway_ip],
            capture_output=True, text=True, timeout=count * timeout + 5
        )
        output = result.stdout

        # Trích xuất từng dòng RTT: "64 bytes from ...: icmp_seq=1 ttl=64 time=1.23 ms"
        rtt_values = [float(m) for m in re.findall(r"time[=<]([\d.]+)\s*ms", output)]

        # Trích xuất tỷ lệ mất gói: "3 packets transmitted, 3 received, 0% packet loss"
        loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", output)
        packet_loss = float(loss_match.group(1)) if loss_match else 0.0

        return rtt_values, packet_loss

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logging.warning(f"[QoE Recorder] Lỗi ping {gateway_ip}: {e}")
        return [], 100.0


def _compute_metrics(rtt_values, packet_loss_raw):
    """
    Tính toán các metric QoE từ danh sách RTT.

    Returns:
        dict với các key tương ứng QOE_CSV_HEADERS (trừ Timestamp)
    """
    if not rtt_values:
        return {
            "latency_min_ms": 0.0,
            "latency_avg_ms": 0.0,
            "latency_max_ms": 0.0,
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
            "latency_p99_ms": 0.0,
            "jitter_ms": 0.0,
            "packet_loss_pct": packet_loss_raw,
        }

    arr = np.array(rtt_values)
    # Jitter = mean of absolute differences giữa các RTT liên tiếp (RFC 3550)
    jitter = float(np.mean(np.abs(np.diff(arr)))) if len(arr) > 1 else 0.0

    return {
        "latency_min_ms": float(np.min(arr)),
        "latency_avg_ms": float(np.mean(arr)),
        "latency_max_ms": float(np.max(arr)),
        "latency_p50_ms": float(np.percentile(arr, 50)),
        "latency_p95_ms": float(np.percentile(arr, 95)),
        "latency_p99_ms": float(np.percentile(arr, 99)),
        "jitter_ms": jitter,
        "packet_loss_pct": packet_loss_raw,
    }


def _recording_loop(gateway_ip, interval, ping_count):
    """Vòng lặp chính ghi nhận QoE metrics."""
    _ensure_csv()
    logging.info(
        f"[QoE Recorder] ✅ Bắt đầu ghi nhận QoE — "
        f"Gateway: {gateway_ip}, Chu kỳ: {interval}s, Ping count: {ping_count}"
    )

    while True:
        try:
            rtt_values, packet_loss = _ping_gateway(gateway_ip, count=ping_count)
            metrics = _compute_metrics(rtt_values, packet_loss)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with open(QOE_CSV_PATH, mode="a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    now_str,
                    f"{metrics['latency_min_ms']:.2f}",
                    f"{metrics['latency_avg_ms']:.2f}",
                    f"{metrics['latency_max_ms']:.2f}",
                    f"{metrics['latency_p50_ms']:.2f}",
                    f"{metrics['latency_p95_ms']:.2f}",
                    f"{metrics['latency_p99_ms']:.2f}",
                    f"{metrics['jitter_ms']:.2f}",
                    f"{metrics['packet_loss_pct']:.2f}",
                ])

            p99 = metrics['latency_p99_ms']
            loss = metrics['packet_loss_pct']
            status_lat = "✅" if p99 < 15.0 else "⚠️"
            status_loss = "✅" if loss < 1.0 else "⚠️"
            logging.info(
                f"[QoE] {status_lat} P99={p99:.1f}ms | "
                f"{status_loss} Loss={loss:.1f}% | "
                f"Avg={metrics['latency_avg_ms']:.1f}ms Jitter={metrics['jitter_ms']:.2f}ms"
            )

        except Exception as e:
            logging.error(f"[QoE Recorder] Lỗi: {e}")

        time.sleep(interval)


def start(gateway_ip="192.168.2.1", interval=3, ping_count=10):
    """
    Khởi động QoE Recorder dưới dạng daemon thread.
    Gọi hàm này từ deploy_hil_agent.py.

    Args:
        gateway_ip: IP gateway/router để ping đo latency
        interval: Chu kỳ đo (giây) — mỗi interval giây sẽ ghi 1 dòng
        ping_count: Số gói ICMP ping mỗi lần đo
    """
    global _recorder_started
    with _recorder_lock:
        if _recorder_started:
            logging.info("[QoE Recorder] Đã chạy rồi, bỏ qua khởi động lại.")
            return
        _recorder_started = True

    t = threading.Thread(
        target=_recording_loop,
        args=(gateway_ip, interval, ping_count),
        daemon=True,
        name="QoE-Recorder"
    )
    t.start()
    logging.info(f"[QoE Recorder] 🚀 Thread đã khởi động (gateway={gateway_ip}).")

