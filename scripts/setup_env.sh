#!/bin/bash
# Script tự động cài đặt môi trường cho Edge Server (Ubuntu WSL2)
# Chạy bằng lệnh: source scripts/setup_env.sh

echo "======================================================"
echo "🚀 KHỞI TẠO MÔI TRƯỜNG KHKT 2026 COGNITIVE WI-FI 🚀"
echo "======================================================"

# 1. Cập nhật hệ thống và cài đặt Tshark (Nếu chưa có)
echo "\n[*] Đang kiểm tra và cài đặt các công cụ hệ thống (Tshark, python3-venv)..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y tshark python3-venv python3-pip

# 2. Khởi tạo Môi trường Ảo Python (Virtual Environment)
ENV_DIR="env"
if [ ! -d "$ENV_DIR" ]; then
    echo "\n[*] Đang tạo Môi trường ảo Python (Virtual Environment)..."
    python3 -m venv $ENV_DIR
else
    echo "\n[*] Môi trường ảo đã tồn tại. Bỏ qua bước tạo mới."
fi

# 3. Kích hoạt môi trường ảo
echo "\n[*] Đang kích hoạt Môi trường ảo..."
source $ENV_DIR/bin/activate

# 4. Cài đặt các thư viện lõi từ requirements.txt
echo "\n[*] Đang nạp hệ sinh thái thư viện (AI, Networking, Data)..."
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "[-] CẢNH BÁO: Không tìm thấy file requirements.txt ở thư mục gốc!"
fi

echo "\n======================================================"
echo "✅ HOÀN TẤT! HỆ THỐNG ĐÃ SẴN SÀNG CHIẾN ĐẤU!"
echo "👉 Hãy đảm bảo bạn luôn thấy chữ '(env)' ở đầu dòng Terminal."
echo "======================================================"