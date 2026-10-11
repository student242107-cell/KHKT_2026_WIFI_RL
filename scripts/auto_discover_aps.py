import yaml
import os
from scapy.all import srp
from scapy.layers.l2 import ARP, Ether

# Đường dẫn lưu file cấu hình mạng (Nằm ở thư mục configs/ đã có sẵn)
CONFIG_PATH = "configs/network_config.yaml"

def discover_network(target_ip="192.168.1.0/24"):
    """
    Sử dụng Scapy ARP Scan để quét toàn bộ dải mạng LAN.
    Trả về một từ điển ánh xạ {MAC: IP}.
    """
    print(f"[*] Đang phóng luồng ARP Scan quét dải mạng {target_ip}...")
    
    # Tạo gói tin ARP Request (Ai có IP này thì trả lời MAC cho tôi)
    arp = ARP(pdst=target_ip)
    ether = Ether(dst="ff:ff:ff:ff:ff:ff") # Gửi Broadcast cho toàn mạng
    packet = ether/arp

    # Phóng gói tin và chờ phản hồi (timeout 2 giây)
    result = srp(packet, timeout=2, verbose=False)[0]

    ap_map = {}
    print(f"[+] Đã tìm thấy {len(result)} thiết bị đang hoạt động:")
    
    for sent, received in result:
        ip = received.psrc
        mac = received.hwsrc.upper() # In hoa MAC cho chuẩn
        
        # --- BỘ LỌC ĐỊNH DANH (OUI FILTER) ---
        # Ở đây bạn có thể lọc chỉ lấy những MAC của hãng Xiaomi/TP-Link
        # Hoặc lọc theo dải IP tĩnh mà bạn đã set cho các AP (VD: IP từ 192.168.1.1 đến 192.168.1.10)
        ip_last_octet = int(ip.split('.')[-1])
        if ip_last_octet <= 10: # Giả sử các AP của bạn được đặt IP tĩnh từ .1 đến .10
            ap_map[mac] = ip
            print(f"    -> [AP Ghi nhận] IP: {ip:15} - MAC: {mac}")
        else:
            print(f"    -> [Bỏ qua Client] IP: {ip:15} - MAC: {mac}")

    return ap_map

def save_to_yaml(ap_map):
    """Lưu từ điển vào file configs/network_config.yaml"""
    # Tạo thư mục configs nếu chưa có
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    
    # Cấu trúc Data chuẩn bị lưu
    data = {
        "network_topology": {
            "ap_map": ap_map
        }
    }
    
    with open(CONFIG_PATH, 'w') as file:
        yaml.dump(data, file, default_flow_style=False)
    
    print(f"\n✅ Đã lưu Cấu trúc Ánh xạ MAC-IP vào: {CONFIG_PATH}")

if __name__ == "__main__":
    # Thay đổi dải IP này cho khớp với Testbed của bạn
    detected_aps = discover_network("192.168.1.0/24")
    if detected_aps:
        save_to_yaml(detected_aps)
    else:
        print("[-] Không tìm thấy AP nào. Vui lòng kiểm tra lại dây LAN.")