#!/bin/bash
# Script khởi chạy Tshark để phân tích Jitter/Packet Loss từ luồng mạng
# Hướng dẫn chạy: sudo ./scripts/tshark_monitor.sh

echo "[*] Khởi động hệ thống giám sát mạng Tshark..."

# Đảm bảo thư mục raw tồn tại
mkdir -p data/raw

# Xóa file log cũ nếu có để tránh phình to dữ liệu
> data/raw/tshark_raw.txt

# Cấu hình cổng mạng (Thay 'eth0' bằng cổng LAN đang cắm vào Router Master)
INTERFACE="eth0"

echo "[+] Đang lắng nghe trên cổng $INTERFACE..."
echo "[+] Log dữ liệu thô đang được ghi vào data/raw/tshark_raw.txt"

# Chạy tshark ẩn dưới background (&). 
# Lệnh -z rtp,streams yêu cầu Tshark tổng hợp các luồng media (UDP/RTP)
sudo tshark -i $INTERFACE -q -z rtp,streams > data/raw/tshark_raw.txt 2>&1 &

# Lưu lại Process ID (PID) để sau này dễ tắt
echo $! > data/raw/tshark_pid.txt
echo "[+] Tshark Daemon đang chạy (PID: $(cat data/raw/tshark_pid.txt))"

# QUAN TRONG: 
# Lưu ý trên WSL2: Bạn phải đảm bảo WSL2 có thể capture được traffic của card mạng Ethernet. 
# Đôi khi cần chạy WSL với quyền Admin.