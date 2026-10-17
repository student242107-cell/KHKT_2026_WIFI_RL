#!/bin/bash
# =====================================================================
# 🧪 CHẠY TOÀN BỘ KIỂM THỬ STRESS - TỰ ĐỘNG TỪ A ĐẾN Z
# =====================================================================
# Script này thực hiện:
#   1. Thiết lập máy ảo OpenWrt (tải firmware, tạo VM)
#   2. Khởi động VM ở chế độ nền
#   3. Đợi VM boot xong và thiết lập SSH
#   4. Chạy bộ kiểm thử stress Python
#   5. Thu thập kết quả
# =====================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VM_DIR="${SCRIPT_DIR}/vm_data"
PID_FILE="${VM_DIR}/openwrt_vm.pid"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

header() {
    echo ""
    echo -e "${CYAN}=================================================================${NC}"
    echo -e "${BOLD}${CYAN} $1${NC}"
    echo -e "${CYAN}=================================================================${NC}"
}

check_dependency() {
    if ! command -v "$1" &>/dev/null; then
        echo -e "${RED}❌ Chưa cài '$1'. Chạy:${NC}"
        echo -e "${YELLOW}   sudo bash ${SCRIPT_DIR}/install_dependencies.sh${NC}"
        exit 1
    fi
}

cleanup() {
    echo -e "\n${YELLOW}🧹 Dọn dẹp...${NC}"
    if [ -f "${PID_FILE}" ]; then
        VM_PID=$(cat "${PID_FILE}")
        if kill -0 "${VM_PID}" 2>/dev/null; then
            echo -e "  Đang tắt VM (PID: ${VM_PID})..."
            kill "${VM_PID}" 2>/dev/null || true
        fi
        rm -f "${PID_FILE}"
    fi
}

# Bắt tín hiệu để dọn dẹp khi Ctrl+C
trap cleanup EXIT

# =====================================================================
header "🧪 KHKT 2026 - KIỂM THỬ SỨC CHỊU TẢI SOFT FAILSAFE"
# =====================================================================

# [0] Kiểm tra dependencies
echo -e "${YELLOW}[0/5] Kiểm tra dependencies...${NC}"
check_dependency "qemu-system-x86_64"
check_dependency "wget"
echo -e "${GREEN}  ✅ Tất cả dependencies đã sẵn sàng.${NC}"

# [1] Thiết lập VM
header "📦 [1/5] THIẾT LẬP MÁY ẢO OPENWRT"
bash "${SCRIPT_DIR}/setup_openwrt_vm.sh"

# [2] Khởi động VM
header "🚀 [2/5] KHỞI ĐỘNG MÁY ẢO"

# Kiểm tra VM đã chạy chưa
if [ -f "${PID_FILE}" ]; then
    OLD_PID=$(cat "${PID_FILE}")
    if kill -0 "${OLD_PID}" 2>/dev/null; then
        echo -e "${GREEN}  VM đã đang chạy (PID: ${OLD_PID}). Tiếp tục...${NC}"
    else
        rm -f "${PID_FILE}"
        bash "${SCRIPT_DIR}/start_openwrt_vm_daemon.sh"
    fi
else
    bash "${SCRIPT_DIR}/start_openwrt_vm_daemon.sh"
fi

# [3] Đợi VM boot hoàn tất
header "⏳ [3/5] ĐỢI VM KHỞI ĐỘNG HOÀN TẤT"
echo -e "${YELLOW}  Đợi OpenWrt boot (tối đa 90 giây)...${NC}"

MAX_WAIT=90
WAITED=0
SSH_READY=false

while [ ${WAITED} -lt ${MAX_WAIT} ]; do
    # Thử kết nối SSH
    if sshpass -p '' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=3 \
        -p 2222 root@127.0.0.1 "echo VM_READY" 2>/dev/null | grep -q "VM_READY"; then
        SSH_READY=true
        break
    fi
    
    echo -ne "\r  ⏳ Đã đợi ${WAITED}s / ${MAX_WAIT}s..."
    sleep 5
    WAITED=$((WAITED + 5))
done

echo ""

if [ "${SSH_READY}" = true ]; then
    echo -e "${GREEN}  ✅ VM đã sẵn sàng! SSH hoạt động. (${WAITED}s)${NC}"
    
    # Hiển thị thông tin VM
    echo -e "${CYAN}  Thông tin VM:${NC}"
    sshpass -p '' ssh -o StrictHostKeyChecking=no -p 2222 root@127.0.0.1 \
        "echo '    Hostname:' \$(hostname); echo '    Kernel:' \$(uname -r); echo '    RAM:' \$(free -m | grep Mem | awk '{print \$2}')MB" 2>/dev/null || true
    
    # Thiết lập mật khẩu SSH (nếu cần cho paramiko)
    echo -e "${YELLOW}  Thiết lập SSH cho kiểm thử...${NC}"
    sshpass -p '' ssh -o StrictHostKeyChecking=no -p 2222 root@127.0.0.1 \
        "echo -e '\n\n' | passwd root 2>/dev/null || true" 2>/dev/null || true
    
    # Cài stress-ng trên VM
    echo -e "${YELLOW}  Cài đặt stress-ng trên VM...${NC}"
    sshpass -p '' ssh -o StrictHostKeyChecking=no -p 2222 root@127.0.0.1 \
        "opkg update 2>/dev/null && opkg install stress-ng 2>/dev/null && echo 'STRESS_NG_OK'" 2>/dev/null | grep -q "STRESS_NG_OK" && \
        echo -e "${GREEN}  ✅ stress-ng đã cài trên VM.${NC}" || \
        echo -e "${YELLOW}  ⚠️ Không cài được stress-ng (sẽ thử lại trong test).${NC}"
else
    echo -e "${RED}  ❌ VM không phản hồi SSH sau ${MAX_WAIT}s.${NC}"
    echo -e "${YELLOW}  Thử kết nối thủ công: ssh root@localhost -p 2222${NC}"
    echo -e "${YELLOW}  Hoặc xem log: cat ${VM_DIR}/openwrt_vm.log${NC}"
    exit 1
fi

# [4] Chạy kiểm thử Python
header "🧪 [4/5] CHẠY BỘ KIỂM THỬ STRESS"

cd "${PROJECT_ROOT}"

# Kích hoạt venv nếu có
if [ -d "env" ]; then
    source env/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Đảm bảo thư mục output tồn tại
mkdir -p data/processed

echo -e "${CYAN}  Bắt đầu chạy 6 test cases...${NC}"
echo ""

python3 tests/openwrt_vm/test_failsafe_stress.py

# [5] Tóm tắt kết quả
header "📊 [5/5] KẾT QUẢ KIỂM THỬ"

# Tìm file report mới nhất
LATEST_REPORT=$(ls -t data/processed/stress_test_report_*.json 2>/dev/null | head -1)
if [ -n "${LATEST_REPORT}" ]; then
    echo -e "${GREEN}  📁 Báo cáo chi tiết: ${LATEST_REPORT}${NC}"
    echo -e "${CYAN}  Nội dung:${NC}"
    python3 -c "
import json
with open('${LATEST_REPORT}') as f:
    r = json.load(f)
s = r['summary']
print(f\"  Total: {s['total']} | Pass: {s['pass']} | Fail: {s['fail']} | Error: {s['error']}\")
" 2>/dev/null || true
fi

echo ""
echo -e "${GREEN}  ✅ Hoàn tất kiểm thử sức chịu tải!${NC}"
echo -e "${YELLOW}  Log: data/processed/stress_test_results.log${NC}"
echo ""
