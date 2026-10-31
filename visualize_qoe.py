#!/usr/bin/env python3
"""
visualize_qoe.py — Hiển thị biểu đồ cam kết QoE của HIL Agent.

Chạy bất cứ lúc nào (kể cả khi deploy_hil_agent đang chạy hoặc đã dừng):
    python visualize_qoe.py               # Hiện toàn bộ dữ liệu
    python visualize_qoe.py --last 60     # Chỉ hiện 60 phút gần nhất
    python visualize_qoe.py --save        # Lưu biểu đồ ra file PNG thay vì hiển thị

Biểu đồ:
  1. Latency P99 theo thời gian — cam kết < 15ms
  2. Packet Loss theo thời gian — cam kết < 1%
"""

import argparse
import os
import sys

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from datetime import timedelta

# ═══════════════════════════════════════════
# CẤU HÌNH
# ═══════════════════════════════════════════
QOE_CSV_PATH = "data/processed/qoe_metrics.csv"
OUTPUT_DIR = "data/processed"

# Ngưỡng cam kết QoE
LATENCY_P99_THRESHOLD_MS = 15.0
PACKET_LOSS_THRESHOLD_PCT = 1.0

# Style
COLORS = {
    "latency_p99": "#E53935",       # Đỏ đậm
    "latency_avg": "#1E88E5",       # Xanh dương
    "latency_fill": "#BBDEFB",      # Xanh nhạt (fill area)
    "loss": "#E53935",              # Đỏ
    "threshold": "#FF9800",         # Cam (đường ngưỡng)
    "safe_zone": "#E8F5E9",         # Xanh lá nhạt (vùng an toàn)
    "danger_zone": "#FFEBEE",       # Đỏ nhạt (vùng vi phạm)
    "jitter": "#AB47BC",            # Tím
}


def load_qoe_data(last_minutes=None):
    """Đọc dữ liệu QoE từ CSV."""
    if not os.path.exists(QOE_CSV_PATH):
        print(f"❌ Không tìm thấy file dữ liệu: {QOE_CSV_PATH}")
        print("   Hãy chạy deploy_hil_agent.py trước để bắt đầu ghi dữ liệu QoE.")
        sys.exit(1)

    df = pd.read_csv(QOE_CSV_PATH)

    if df.empty:
        print("❌ File dữ liệu QoE rỗng. Hãy đợi vài giây sau khi bật HIL Agent.")
        sys.exit(1)

    df["Timestamp"] = pd.to_datetime(df["Timestamp"])

    # Lọc theo khoảng thời gian nếu có
    if last_minutes:
        cutoff = df["Timestamp"].max() - timedelta(minutes=last_minutes)
        df = df[df["Timestamp"] >= cutoff]

    if df.empty:
        print(f"❌ Không có dữ liệu trong {last_minutes} phút gần nhất.")
        sys.exit(1)

    return df


def _format_time_axis(ax, df):
    """Định dạng trục thời gian thông minh dựa trên khoảng thời gian dữ liệu."""
    duration = (df["Timestamp"].max() - df["Timestamp"].min()).total_seconds()

    if duration < 600:  # < 10 phút
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax.xaxis.set_major_locator(mdates.SecondLocator(interval=max(1, int(duration / 10))))
    elif duration < 7200:  # < 2 giờ
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=max(1, int(duration / 600))))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, int(duration / 36000))))

    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=8)


def _compute_compliance(values, threshold, mode="below"):
    """Tính tỷ lệ tuân thủ cam kết (% giá trị nằm trong ngưỡng)."""
    if mode == "below":
        compliant = np.sum(values < threshold)
    else:
        compliant = np.sum(values > threshold)
    return (compliant / len(values)) * 100.0


def plot_latency(ax, df):
    """Vẽ biểu đồ Latency P99 + Avg + Jitter."""
    ts = df["Timestamp"]
    p99 = df["latency_p99_ms"]
    avg = df["latency_avg_ms"]
    jitter = df["jitter_ms"]

    # Vùng an toàn (dưới ngưỡng)
    ax.axhspan(0, LATENCY_P99_THRESHOLD_MS, color=COLORS["safe_zone"], alpha=0.4, zorder=0)

    # Vùng vi phạm (trên ngưỡng) — chỉ hiện nếu có dữ liệu vượt ngưỡng
    y_max = max(p99.max() * 1.15, LATENCY_P99_THRESHOLD_MS * 1.5)
    ax.axhspan(LATENCY_P99_THRESHOLD_MS, y_max, color=COLORS["danger_zone"], alpha=0.3, zorder=0)

    # Đường ngưỡng cam kết
    ax.axhline(
        y=LATENCY_P99_THRESHOLD_MS, color=COLORS["threshold"],
        linestyle="--", linewidth=2.0, alpha=0.9, zorder=5,
        label=f"Cam kết SLA: P99 < {LATENCY_P99_THRESHOLD_MS:.0f}ms"
    )

    # Fill giữa avg và p99
    ax.fill_between(ts, avg, p99, color=COLORS["latency_fill"], alpha=0.4, zorder=2)

    # Đường Latency Avg
    ax.plot(
        ts, avg, color=COLORS["latency_avg"], linewidth=1.5,
        alpha=0.7, label="Latency Trung bình", zorder=3
    )

    # Đường Latency P99 (nổi bật nhất)
    ax.plot(
        ts, p99, color=COLORS["latency_p99"], linewidth=2.2,
        marker=".", markersize=3, label="Latency P99", zorder=4
    )

    # Đường Jitter
    ax.plot(
        ts, jitter, color=COLORS["jitter"], linewidth=1.2,
        alpha=0.6, linestyle="-.", label="Jitter (RFC 3550)", zorder=3
    )

    # Tỷ lệ tuân thủ
    compliance = _compute_compliance(p99.values, LATENCY_P99_THRESHOLD_MS)
    ax.set_title(
        f"📊 Độ trễ Mạng — Latency P99 vs. Cam kết SLA\n"
        f"(Tỷ lệ tuân thủ P99 < {LATENCY_P99_THRESHOLD_MS:.0f}ms: {compliance:.1f}%)",
        fontsize=12, fontweight="bold", pad=12
    )
    ax.set_ylabel("Latency (ms)", fontsize=10)
    ax.set_xlabel("Thời gian", fontsize=10)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax.set_ylim(bottom=0, top=y_max)
    ax.grid(True, alpha=0.3, linestyle=":")
    _format_time_axis(ax, df)

    # Thêm annotation thống kê
    stats_text = (
        f"P99 max: {p99.max():.1f}ms\n"
        f"P99 avg: {p99.mean():.1f}ms\n"
        f"Jitter avg: {jitter.mean():.2f}ms\n"
        f"Samples: {len(df)}"
    )
    ax.text(
        0.02, 0.97, stats_text,
        transform=ax.transAxes, fontsize=7.5,
        verticalalignment="top", fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.85, edgecolor="#ccc")
    )


def plot_packet_loss(ax, df):
    """Vẽ biểu đồ Packet Loss."""
    ts = df["Timestamp"]
    loss = df["packet_loss_pct"]

    # Vùng an toàn
    ax.axhspan(0, PACKET_LOSS_THRESHOLD_PCT, color=COLORS["safe_zone"], alpha=0.4, zorder=0)

    # Vùng vi phạm
    y_max = max(loss.max() * 1.15, PACKET_LOSS_THRESHOLD_PCT * 3, 2.0)
    ax.axhspan(PACKET_LOSS_THRESHOLD_PCT, y_max, color=COLORS["danger_zone"], alpha=0.3, zorder=0)

    # Đường ngưỡng cam kết
    ax.axhline(
        y=PACKET_LOSS_THRESHOLD_PCT, color=COLORS["threshold"],
        linestyle="--", linewidth=2.0, alpha=0.9, zorder=5,
        label=f"Cam kết SLA: Packet Loss < {PACKET_LOSS_THRESHOLD_PCT:.0f}%"
    )

    # Fill area dưới đường packet loss
    ax.fill_between(ts, 0, loss, color="#FFCDD2", alpha=0.35, zorder=2)

    # Đường Packet Loss
    ax.plot(
        ts, loss, color=COLORS["loss"], linewidth=2.0,
        marker=".", markersize=4, label="Packet Loss (%)", zorder=4
    )

    # Đánh dấu các điểm vi phạm (nếu có)
    violations = df[loss > PACKET_LOSS_THRESHOLD_PCT]
    if not violations.empty:
        ax.scatter(
            violations["Timestamp"], violations["packet_loss_pct"],
            color="red", s=50, zorder=6, marker="x", linewidths=2,
            label=f"Vi phạm SLA ({len(violations)} lần)"
        )

    # Tỷ lệ tuân thủ
    compliance = _compute_compliance(loss.values, PACKET_LOSS_THRESHOLD_PCT)
    ax.set_title(
        f"📊 Tỷ lệ Rớt gói — Packet Loss vs. Cam kết SLA\n"
        f"(Tỷ lệ tuân thủ Loss < {PACKET_LOSS_THRESHOLD_PCT:.0f}%: {compliance:.1f}%)",
        fontsize=12, fontweight="bold", pad=12
    )
    ax.set_ylabel("Packet Loss (%)", fontsize=10)
    ax.set_xlabel("Thời gian", fontsize=10)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax.set_ylim(bottom=-0.05, top=y_max)
    ax.grid(True, alpha=0.3, linestyle=":")
    _format_time_axis(ax, df)

    # Thêm annotation thống kê
    stats_text = (
        f"Loss max: {loss.max():.2f}%\n"
        f"Loss avg: {loss.mean():.2f}%\n"
        f"Violations: {len(violations)}/{len(df)}\n"
        f"Samples: {len(df)}"
    )
    ax.text(
        0.02, 0.97, stats_text,
        transform=ax.transAxes, fontsize=7.5,
        verticalalignment="top", fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.85, edgecolor="#ccc")
    )


def main():
    parser = argparse.ArgumentParser(
        description="📈 Biểu đồ cam kết QoE của HIL Agent (Latency P99 < 15ms, Packet Loss < 1%)"
    )
    parser.add_argument(
        "--last", type=int, default=None,
        help="Chỉ hiển thị N phút gần nhất (mặc định: toàn bộ)"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Lưu biểu đồ ra file PNG thay vì hiển thị cửa sổ"
    )
    parser.add_argument(
        "--csv", type=str, default=QOE_CSV_PATH,
        help=f"Đường dẫn file CSV (mặc định: {QOE_CSV_PATH})"
    )
    args = parser.parse_args()

    # Nếu save mode, dùng backend Agg (không cần GUI)
    if args.save:
        matplotlib.use("Agg")

    global QOE_CSV_PATH
    QOE_CSV_PATH = args.csv

    # Đọc dữ liệu
    df = load_qoe_data(last_minutes=args.last)

    time_range = f"{df['Timestamp'].min().strftime('%H:%M:%S')} → {df['Timestamp'].max().strftime('%H:%M:%S')}"
    duration_mins = (df["Timestamp"].max() - df["Timestamp"].min()).total_seconds() / 60.0

    print(f"📈 QoE Visualization — {len(df)} samples | {time_range} ({duration_mins:.1f} phút)")

    # Tạo figure với 2 subplot xếp dọc
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), dpi=100)
    fig.suptitle(
        f"🏫 KHKT 2026 — Cam kết QoE khi HIL Agent can thiệp\n"
        f"Khoảng thời gian: {df['Timestamp'].min().strftime('%d/%m/%Y %H:%M')} → "
        f"{df['Timestamp'].max().strftime('%d/%m/%Y %H:%M')} "
        f"({duration_mins:.0f} phút, {len(df)} mẫu)",
        fontsize=13, fontweight="bold", y=0.99
    )

    plot_latency(ax1, df)
    plot_packet_loss(ax2, df)

    plt.tight_layout(rect=[0, 0, 1, 0.94])

    if args.save:
        out_latency = os.path.join(OUTPUT_DIR, "qoe_latency.png")
        out_loss = os.path.join(OUTPUT_DIR, "qoe_packet_loss.png")

        # Lưu biểu đồ tổng hợp
        combined_path = os.path.join(OUTPUT_DIR, "qoe_combined.png")
        fig.savefig(combined_path, dpi=150, bbox_inches="tight", facecolor="white")
        print(f"   ✅ Đã lưu biểu đồ tổng hợp: {combined_path}")

        # Lưu từng biểu đồ riêng
        fig_lat, ax_lat = plt.subplots(1, 1, figsize=(14, 5), dpi=100)
        plot_latency(ax_lat, df)
        fig_lat.tight_layout()
        fig_lat.savefig(out_latency, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig_lat)
        print(f"   ✅ Đã lưu biểu đồ latency: {out_latency}")

        fig_loss, ax_loss = plt.subplots(1, 1, figsize=(14, 5), dpi=100)
        plot_packet_loss(ax_loss, df)
        fig_loss.tight_layout()
        fig_loss.savefig(out_loss, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig_loss)
        print(f"   ✅ Đã lưu biểu đồ packet loss: {out_loss}")

        plt.close(fig)
    else:
        plt.show()


if __name__ == "__main__":
    main()

