#!/bin/bash
# ==============================================================================
# SCRIPT CHUYỂN ĐỔI USB WI-FI SANG CHẾ ĐỘ MONITOR MODE & INJECTION
# Dùng cho chip RTL8821AU (TP-Link Archer T2U Plus) hoặc tương đương trên Linux
# ==============================================================================

# Tên card mạng Wi-Fi mặc định khi vừa cắm vào (có thể dùng lệnh 'iwconfig' để kiểm tra)
WIFI_IFACE="wlan0"
MON_IFACE="wlan0mon"

echo "[*] Đang ngắt các tiến trình mạng gây nhiễu (NetworkManager, wpa_supplicant)..."
# airmon-ng là công cụ tối thượng (nằm trong bộ aircrack-ng) để kill các tiến trình chiếm dụng card Wi-Fi
sudo airmon-ng check kill

echo "[*] Đang đưa card $WIFI_IFACE xuống trạng thái tắt (down)..."
sudo ip link set $WIFI_IFACE down

echo "[*] Đang chuyển đổi sang chế độ Giám sát (Monitor Mode)..."
sudo iw dev $WIFI_IFACE set type monitor

echo "[*] Đang bật lại card mạng dưới tên $MON_IFACE..."
# Đôi khi lệnh 'iw' đổi tên thành wlan0mon, đôi khi nó giữ nguyên wlan0 nhưng ở mode monitor
# Ta đổi tên cho chuẩn với code Python của Dũng
sudo ip link set $WIFI_IFACE name $MON_IFACE
sudo ip link set $MON_IFACE up

echo "[+] HOÀN TẤT! USB Wi-Fi của bạn đã biến thành một Radar thực thụ."
echo "Hãy kiểm tra bằng lệnh: iwconfig"