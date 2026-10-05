import os
import sys
from scapy.all import sniff
# sniff(): capture network packets in real-time
from scapy.layers.dot11 import Dot11Beacon
from scapy.layers.dot11 import Dot11
# dot11 beacon: sent by a Wifi AP to broacast 
# its network presence, data rates, operational parameters 

def process_packet(packet) -> None:
    # Filter the specific Management Frames: Dot11 Beacon
    if packet.haslayer(Dot11Beacon):
        # SSID: Network name
        ssid = packet.info.decode('utf-8', errors='ignore')

        # BSSID: MAC address of the router
        bssid = packet.addr2

        if not ssid: # SSID == NULL --> Hidden network
            print(f"Hidden Network detected with MAC: {bssid}")
        else:
            print(f"Detected Wifi: {ssid}, MAC Address(BSSID): {bssid}")

def process_mac_frames(packet):
    global stats
    if packet.haslayer(Dot11):
        # Type 2: Data Frame | Type 1: Control Frame
        # Subtype 0x1D (thập phân 29) đại diện cho khung ACK trong 802.11 Control Frames
        frame_type = packet.type
        frame_subtype = packet.subtype
        
        # Kiểm tra cờ Retry (bít thứ 11 trong trường Frame Control)
        # Bít này bật lên 1 khi trạm phải truyền lại gói do không nhận được ACK trước đó
        is_retry = packet.FCfield & 0x08 != 0
        
        if frame_type == 2: # Data Frame
            stats["data_frames"] += 1
            if is_retry:
                stats["retry_frames"] += 1
        elif frame_type == 1 and frame_subtype == 0x1D: # Control Frame: ACK
            stats["ack_frames"] += 1

stats = {
    "data_frames": 0,
    "ack_frames": 0,
    "retry_frames": 0
}

def quick_csma_efficiency_check(data_count, ack_count, retry_count):
    """
    Hàm kiểm tra nhanh (Sanity Check) tình trạng đụng độ tầng MAC (CSMA/CA).
    Đánh giá tỷ lệ truyền thành công và phát hiện tắc nghẽn do va chạm sóng.
    """
    print("\n[*] --- KIỂM TRA NHANH TẮC NGHẼN TẦNG MAC (CSMA/CA HEALTH) ---")
    total = data_count + ack_count
    if total == 0:
        print("[-] Chưa thu thập đủ dữ liệu khung truyền.")
        return False

    if data_count>0:    
        retry_ratio = (retry_count / data_count) 
    else: 
        retry_ratio = 0

    print(f"    Data Frames: {data_count} | ACK Frames: {ack_count} | Gói truyền lại (Retry): {retry_count}")
    print(f"    Tỷ lệ truyền lại (Collision Indicator): {retry_ratio * 100:.2f}%")
    
    if retry_ratio > 0.20: # Nếu trên 20% gói tin phải truyền lại
        print("    [!] CẢNH BÁO NGHẼN MẠNG: Xung đột CSMA/CA cao hoặc xuất hiện Trạm ẩn (Hidden Terminal)!")
        return False
    else:
        print("    [+] Trạng thái MAC ổn định. Kênh truyền thông thoáng.")
        return True

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Error: You must run the script with sudo: sudo python3 test_sniff.py")
    sys.exit(1)

    print("Detecting... (Ctrl+C to stop)")
    try:
        sniff(iface = "wlan0", prn = process_packet, 
      store = False, count = 10)
    except Exception as e:
        print(f"Lỗi khi truy cập card Vô Tuyến: {e}")
# iface: network interface name
# prn: callback function
# store: whether to store packets in memory
# count: number of packets to capture before stopping.