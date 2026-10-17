#!/bin/bash
# =====================================================================
# CÀI ĐẶT TẤT CẢ DEPENDENCIES CHO KIỂM THỬ STRESS - OPENWRT VM
# =====================================================================
# Chạy script này 1 lần duy nhất với quyền sudo:
#   sudo bash tests/openwrt_vm/install_dependencies.sh
# =====================================================================

set -e

echo "================================================================="
echo " 📦 CÀI ĐẶT DEPENDENCIES - KHKT 2026 STRESS TEST"
echo "================================================================="

# Kiểm tra quyền root
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Cần chạy với quyền sudo!"
    echo "   Ví dụ: sudo bash $0"
    exit 1
fi

echo "[1/3] Cập nhật danh sách gói..."
apt-get update -qq

echo "[2/3] Cài đặt QEMU, stress-ng, và các công cụ hỗ trợ..."
apt-get install -y -qq \
    qemu-system-x86 \
    qemu-utils \
    stress-ng \
    wget \
    sshpass \
    expect

echo "[3/3] Kiểm tra cài đặt..."
echo "  QEMU:      $(qemu-system-x86_64 --version 2>/dev/null | head -1 || echo 'N/A')"
echo "  stress-ng: $(stress-ng --version 2>/dev/null || echo 'N/A')"
echo "  sshpass:   $(which sshpass 2>/dev/null || echo 'N/A')"

echo ""
echo "✅ Hoàn tất cài đặt dependencies!"
echo "   Tiếp theo: bash tests/openwrt_vm/setup_openwrt_vm.sh"
