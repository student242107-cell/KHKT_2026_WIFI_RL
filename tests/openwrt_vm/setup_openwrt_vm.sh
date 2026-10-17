#!/bin/bash
# =====================================================================
# SETUP OPENWRT VIRTUAL MACHINE - KHKT 2026 WIFI RL
# =====================================================================
# Script tự động tải firmware OpenWrt, tạo ổ đĩa ảo, và khởi chạy
# máy ảo QEMU để kiểm thử Failsafe Watchdog dưới tải nặng.
# =====================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM_DIR="${SCRIPT_DIR}/vm_data"
OPENWRT_VERSION="23.05.5"
OPENWRT_IMAGE="openwrt-${OPENWRT_VERSION}-x86-64-generic-ext4-combined.img"
OPENWRT_URL="https://downloads.openwrt.org/releases/${OPENWRT_VERSION}/targets/x86/64/${OPENWRT_IMAGE}.gz"

# --- Cấu hình VM ---
VM_RAM="256"          # RAM (MB) - đủ cho OpenWrt, dễ ép 100%
VM_CPUS="1"           # Số CPU ảo - dễ ép 100%
SSH_HOST_PORT="2222"  # Port forward SSH từ host vào VM
HTTP_HOST_PORT="8080" # Port forward LuCI HTTP
VM_DISK_SIZE="256M"   # Kích thước ổ đĩa overlay

# Màu sắc terminal
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}=================================================================${NC}"
echo -e "${CYAN} 🖥️  OPENWRT VM SETUP - KHKT 2026 COGNITIVE WIFI${NC}"
echo -e "${CYAN}=================================================================${NC}"

# 1. Tạo thư mục VM
mkdir -p "${VM_DIR}"
cd "${VM_DIR}"

# 2. Tải firmware OpenWrt nếu chưa có
if [ ! -f "${OPENWRT_IMAGE}" ]; then
    echo -e "${YELLOW}[1/4] Đang tải firmware OpenWrt ${OPENWRT_VERSION}...${NC}"
    
    if [ -f "${OPENWRT_IMAGE}.gz" ]; then
        echo -e "${GREEN}  → Đã có file nén, bỏ qua tải.${NC}"
    else
        wget -q --show-progress "${OPENWRT_URL}" -O "${OPENWRT_IMAGE}.gz"
    fi
    
    echo -e "${YELLOW}  → Giải nén firmware...${NC}"
    gunzip -k "${OPENWRT_IMAGE}.gz" 2>/dev/null || gunzip "${OPENWRT_IMAGE}.gz"
    echo -e "${GREEN}  ✅ Đã giải nén firmware thành công.${NC}"
else
    echo -e "${GREEN}[1/4] Firmware OpenWrt đã tồn tại, bỏ qua tải.${NC}"
fi

# 3. Mở rộng image để có đủ dung lượng cài stress-ng
echo -e "${YELLOW}[2/4] Mở rộng ổ đĩa ảo...${NC}"
qemu-img resize "${OPENWRT_IMAGE}" "${VM_DISK_SIZE}" 2>/dev/null || true
echo -e "${GREEN}  ✅ Ổ đĩa ảo: ${VM_DISK_SIZE}${NC}"

# 4. Tạo script khởi động VM
echo -e "${YELLOW}[3/4] Tạo script khởi động VM...${NC}"
cat > "${SCRIPT_DIR}/start_openwrt_vm.sh" << 'VMSCRIPT'
#!/bin/bash
# Khởi động máy ảo OpenWrt QEMU
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM_DIR="${SCRIPT_DIR}/vm_data"

# Tìm image OpenWrt
OPENWRT_IMAGE=$(ls "${VM_DIR}"/openwrt-*-x86-64-generic-ext4-combined.img 2>/dev/null | head -1)

if [ -z "${OPENWRT_IMAGE}" ]; then
    echo "❌ Không tìm thấy firmware OpenWrt. Chạy setup_openwrt_vm.sh trước."
    exit 1
fi

echo "🚀 Khởi động OpenWrt VM..."
echo "   SSH Port: localhost:2222"
echo "   LuCI:    http://localhost:8080"
echo ""
echo "   Để thoát VM: Nhấn Ctrl+A rồi X"
echo ""

qemu-system-x86_64 \
    -m 256 \
    -smp 1 \
    -drive file="${OPENWRT_IMAGE}",format=raw,if=virtio \
    -netdev user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::8080-:80 \
    -device virtio-net-pci,netdev=net0 \
    -nographic \
    -enable-kvm 2>/dev/null || \
qemu-system-x86_64 \
    -m 256 \
    -smp 1 \
    -drive file="${OPENWRT_IMAGE}",format=raw,if=virtio \
    -netdev user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::8080-:80 \
    -device virtio-net-pci,netdev=net0 \
    -nographic
VMSCRIPT
chmod +x "${SCRIPT_DIR}/start_openwrt_vm.sh"

# 5. Tạo script khởi động VM ở chế độ nền (daemon)
cat > "${SCRIPT_DIR}/start_openwrt_vm_daemon.sh" << 'DAEMONSCRIPT'
#!/bin/bash
# Khởi động OpenWrt VM ở chế độ nền (Background/Daemon)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM_DIR="${SCRIPT_DIR}/vm_data"
PID_FILE="${VM_DIR}/openwrt_vm.pid"
LOG_FILE="${VM_DIR}/openwrt_vm.log"

# Tìm image OpenWrt
OPENWRT_IMAGE=$(ls "${VM_DIR}"/openwrt-*-x86-64-generic-ext4-combined.img 2>/dev/null | head -1)

if [ -z "${OPENWRT_IMAGE}" ]; then
    echo "❌ Không tìm thấy firmware OpenWrt. Chạy setup_openwrt_vm.sh trước."
    exit 1
fi

# Kiểm tra VM đã chạy chưa
if [ -f "${PID_FILE}" ]; then
    OLD_PID=$(cat "${PID_FILE}")
    if kill -0 "${OLD_PID}" 2>/dev/null; then
        echo "⚠️  OpenWrt VM đã đang chạy (PID: ${OLD_PID})"
        echo "   Dùng lệnh: kill ${OLD_PID} để tắt VM cũ."
        exit 1
    fi
fi

echo "🚀 Khởi động OpenWrt VM ở chế độ nền..."

# Thử với KVM trước, fallback sang TCG nếu không hỗ trợ
nohup qemu-system-x86_64 \
    -m 256 \
    -smp 1 \
    -drive file="${OPENWRT_IMAGE}",format=raw,if=virtio \
    -netdev user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::8080-:80 \
    -device virtio-net-pci,netdev=net0 \
    -nographic \
    -serial mon:stdio \
    -enable-kvm > "${LOG_FILE}" 2>&1 &

VM_PID=$!
sleep 2

# Kiểm tra VM có khởi động thành công không
if ! kill -0 "${VM_PID}" 2>/dev/null; then
    echo "⚠️  KVM không khả dụng, thử lại với chế độ TCG..."
    nohup qemu-system-x86_64 \
        -m 256 \
        -smp 1 \
        -drive file="${OPENWRT_IMAGE}",format=raw,if=virtio \
        -netdev user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::8080-:80 \
        -device virtio-net-pci,netdev=net0 \
        -nographic \
        -serial mon:stdio > "${LOG_FILE}" 2>&1 &
    VM_PID=$!
    sleep 2
fi

if kill -0 "${VM_PID}" 2>/dev/null; then
    echo "${VM_PID}" > "${PID_FILE}"
    echo "✅ OpenWrt VM đã khởi động thành công!"
    echo "   PID:      ${VM_PID}"
    echo "   SSH:      ssh root@localhost -p 2222"
    echo "   LuCI:     http://localhost:8080"
    echo "   Log:      ${LOG_FILE}"
    echo "   PID File: ${PID_FILE}"
    echo ""
    echo "   Để tắt VM: kill \$(cat ${PID_FILE})"
else
    echo "❌ Không thể khởi động VM. Xem log: ${LOG_FILE}"
    exit 1
fi
DAEMONSCRIPT
chmod +x "${SCRIPT_DIR}/start_openwrt_vm_daemon.sh"

# 6. Tạo script tắt VM
cat > "${SCRIPT_DIR}/stop_openwrt_vm.sh" << 'STOPSCRIPT'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${SCRIPT_DIR}/vm_data/openwrt_vm.pid"

if [ -f "${PID_FILE}" ]; then
    VM_PID=$(cat "${PID_FILE}")
    if kill -0 "${VM_PID}" 2>/dev/null; then
        echo "🛑 Đang tắt OpenWrt VM (PID: ${VM_PID})..."
        kill "${VM_PID}"
        rm -f "${PID_FILE}"
        echo "✅ Đã tắt VM."
    else
        echo "⚠️  VM không còn chạy. Dọn dẹp PID file..."
        rm -f "${PID_FILE}"
    fi
else
    echo "ℹ️  Không tìm thấy PID file. VM có thể chưa được khởi động."
    # Thử tìm process QEMU
    QEMU_PIDS=$(pgrep -f "qemu.*openwrt" 2>/dev/null)
    if [ -n "${QEMU_PIDS}" ]; then
        echo "   Tìm thấy QEMU process: ${QEMU_PIDS}"
        echo "   Dùng: kill ${QEMU_PIDS} để tắt."
    fi
fi
STOPSCRIPT
chmod +x "${SCRIPT_DIR}/stop_openwrt_vm.sh"

echo -e "${GREEN}  ✅ Đã tạo các script quản lý VM.${NC}"

# 7. Hoàn tất
echo -e "${CYAN}=================================================================${NC}"
echo -e "${GREEN}[4/4] ✅ HOÀN TẤT THIẾT LẬP!${NC}"
echo -e "${CYAN}=================================================================${NC}"
echo ""
echo -e "${YELLOW}Hướng dẫn sử dụng:${NC}"
echo ""
echo -e "  ${GREEN}1. Khởi động VM (nền):${NC}"
echo -e "     ${CYAN}bash tests/openwrt_vm/start_openwrt_vm_daemon.sh${NC}"
echo ""
echo -e "  ${GREEN}2. Đợi 30-60s rồi SSH vào VM:${NC}"
echo -e "     ${CYAN}ssh root@localhost -p 2222${NC}"
echo ""
echo -e "  ${GREEN}3. Trong VM, cài stress-ng:${NC}"
echo -e "     ${CYAN}opkg update && opkg install stress-ng${NC}"
echo ""
echo -e "  ${GREEN}4. Chạy kiểm thử tự động:${NC}"
echo -e "     ${CYAN}python tests/openwrt_vm/test_failsafe_stress.py${NC}"
echo ""
echo -e "  ${GREEN}5. Tắt VM:${NC}"
echo -e "     ${CYAN}bash tests/openwrt_vm/stop_openwrt_vm.sh${NC}"
