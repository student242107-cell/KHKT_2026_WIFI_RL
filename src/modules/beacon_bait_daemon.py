import csv
import json
import logging
import os
import re
import subprocess
import threading
import time
from typing import Dict, List, Optional, Set

from dotenv import load_dotenv
from scapy.all import sendp, sniff
from scapy.layers.dot11 import (
    Dot11,
    Dot11AssoReq,
    Dot11Auth,
    Dot11Beacon,
    Dot11Elt,
    Dot11ProbeReq,
    RadioTap,
)
import yaml

import src.modules.wifi_actuators as actuators

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [BEACON-BAIT] - %(message)s")

load_dotenv()
USER = os.getenv("ROUTER_USER", "root")
PWD = os.getenv("ROUTER_PASS", "")

VIP_MAC_FILE = "data/processed/mac_vip_list.json"
AI_ACTIONS_LOG = "data/processed/ai_actions_log.csv"
CONFIG_PATH = "configs/network_config.yaml"

IFACE = os.getenv("MONITOR_IFACE", "wlan0mon")
BAIT_IFACE = os.getenv("BAIT_IFACE", IFACE)
FAKE_BSSID = "02:11:22:33:44:55"
FAKE_SSID = os.getenv("TEACHER_SSID", "School_Teacher")

# Thời gian chờ kiểm tra sau khi không còn thấy tín hiệu (60s khi thi KHKT, 600s thực tế)
WAKE_DURATION = int(os.getenv("TEACHER_WAKE_DURATION", "60"))
INJECT_INTERVAL = int(os.getenv("BAIT_INJECT_INTERVAL", "25"))

# Chuẩn IEEE 802.11i RSN IE (ID=48): WPA2-Personal (PSK) + AES-CCMP (Group & Pairwise)
RSN_WPA2_PSK_CCMP = (
    b"\x01\x00"              # RSN Version 1
    b"\x00\x0f\xac\x04"      # Group Cipher: AES-CCMP-128
    b"\x01\x00"              # Pairwise Cipher Count: 1
    b"\x00\x0f\xac\x04"      # Pairwise Cipher: AES-CCMP-128
    b"\x01\x00"              # AKM Suite Count: 1
    b"\x00\x0f\xac\x02"      # AKM Suite: PSK (WPA2-Personal)
    b"\x00\x00"              # RSN Capabilities
)
SUPPORTED_RATES_IE = b"\x82\x84\x8b\x96\x0c\x12\x18\x24"
EXT_SUPPORTED_RATES_IE = b"\x30\x48\x60\x6c"
NON_DFS_5G_CHANNELS = [36, 40, 44, 48, 149, 153, 157, 161, 165]

ap_lock = threading.Lock()
ap_status: Dict[str, dict] = {}
AP_MAP: Dict[str, str] = {}
PRIVILEGED_OFFICE_IPS: Set[str] = set()


def load_topology_config(config_path: str = CONFIG_PATH) -> None:
    """Đọc danh sách AP, BSSID thật và phân loại vùng Classroom vs. Privileged Office (Always-On)."""
    global AP_MAP, PRIVILEGED_OFFICE_IPS, ap_status, FAKE_BSSID
    if not os.path.exists(config_path):
        return
    try:
        with open(config_path, "r") as file:
            config = yaml.safe_load(file) or {}
            topo = config.get("network_topology", {})
            with ap_lock:
                AP_MAP = {}
                PRIVILEGED_OFFICE_IPS = set()
                ap_status = {}
                for bssid, ip in topo.get("ap_map", {}).items():
                    AP_MAP[bssid.upper()] = ip
                    if FAKE_BSSID == "02:11:22:33:44:55":
                        FAKE_BSSID = bssid.lower()
                for bssid, ip in topo.get("ap_map_2g", {}).items():
                    AP_MAP[bssid.upper()] = ip

                for node in topo.get("nodes", []):
                    ip = node.get("ip")
                    ztype = node.get("zone_type", "classroom").lower()
                    if ztype == "privileged_office":
                        PRIVILEGED_OFFICE_IPS.add(ip)
                    if ip and ip not in ap_status:
                        ap_status[ip] = {
                            "name": node.get("name", ip),
                            "zone_type": ztype,
                            "is_woken": (ztype == "privileged_office"),
                            "timer": None,
                        }
    except Exception as e:
        logging.error(f"[-] Lỗi đọc file YAML: {e}")


def get_vip_macs(vip_file: str = VIP_MAC_FILE) -> Set[str]:
    try:
        if os.path.exists(vip_file):
            with open(vip_file, "r") as f:
                return set(str(m).upper() for m in json.load(f))
    except Exception:
        pass
    return set()


def _probe_supported_channels(iface: str) -> List[int]:
    """Dò các kênh thực sự được card USB Wi-Fi hỗ trợ qua iwlist; fallback về [1, 6, 11]."""
    try:
        res = subprocess.run(
            ["iwlist", iface, "frequency"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        ch_found = []
        for m in re.findall(r"Channel\s+0*(\d+)", res.stdout):
            c = int(m)
            if c in (1, 6, 11) or c in NON_DFS_5G_CHANNELS:
                if c not in ch_found:
                    ch_found.append(c)
        if ch_found:
            return ch_found
    except Exception:
        pass
    return [1, 6, 11]


def _read_latest_ap_5g_channels(log_path: str = AI_ACTIONS_LOG) -> List[int]:
    """Đọc kênh 5GHz gần nhất mà HIL Agent vừa điều phối để ưu tiên quét/bơm đúng kênh đang phục vụ."""
    if not os.path.exists(log_path):
        return []
    channels: List[int] = []
    try:
        with open(log_path, "r", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows[-10:]:
            idx = int(r.get("CH_Idx", -1))
            if 0 <= idx < len(NON_DFS_5G_CHANNELS):
                ch = NON_DFS_5G_CHANNELS[idx]
                if ch not in channels:
                    channels.append(ch)
    except Exception:
        pass
    return channels


def get_candidate_bait_channels(iface: str = BAIT_IFACE) -> List[int]:
    """Kết hợp kênh 5GHz AP đang hoạt động và 2.4GHz tiêu chuẩn (chỉ giữ kênh phần cứng USB hỗ trợ)."""
    supported = set(_probe_supported_channels(iface))
    prioritized = _read_latest_ap_5g_channels() + [36, 44, 149, 1, 6, 11]
    ordered = [c for c in prioritized if c in supported]
    dedup = []
    for c in ordered:
        if c not in dedup:
            dedup.append(c)
    return dedup if dedup else [1, 6, 11]


def build_ieee80211_bait_beacon(
    ssid: str = FAKE_SSID,
    bssid: str = FAKE_BSSID,
    channel: int = 6,
) -> RadioTap:
    """
    Dựng khung Beacon chuẩn IEEE 802.11i WPA2-PSK (AES-CCMP):
      - Có đủ Supported Rates (ID=1), DS Parameter Set (ID=3 khi <=14), Extended Rates (ID=50)
      - Gắn đầy đủ RSN Information Element (ID=48, 20 bytes) khớp `psk2` để iOS/Android/Windows
        nhận diện là mạng WPA2-PSK hợp lệ thay vì từ chối như gói legacy thiếu RSN.
    """
    ssid_bytes = ssid.encode("utf-8")
    dot11 = Dot11(type=0, subtype=8, addr1="ff:ff:ff:ff:ff:ff", addr2=bssid, addr3=bssid)
    beacon = Dot11Beacon(beacon_interval=100, cap="ESS+privacy+short-preamble+short-slot")
    pkt = (
        RadioTap()
        / dot11
        / beacon
        / Dot11Elt(ID="SSID", info=ssid_bytes, len=len(ssid_bytes))
        / Dot11Elt(ID="Rates", info=SUPPORTED_RATES_IE, len=len(SUPPORTED_RATES_IE))
    )
    if 1 <= int(channel) <= 14:
        pkt = pkt / Dot11Elt(ID="DSset", info=bytes([int(channel)]), len=1)
    pkt = (
        pkt
        / Dot11Elt(ID="ESRates", info=EXT_SUPPORTED_RATES_IE, len=len(EXT_SUPPORTED_RATES_IE))
        / Dot11Elt(ID=48, info=RSN_WPA2_PSK_CCMP, len=len(RSN_WPA2_PSK_CCMP))
    )
    return pkt


def _set_iface_channel(iface: str, ch: int) -> bool:
    try:
        res = subprocess.run(
            ["iwconfig", iface, "channel", str(int(ch))],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


# ==========================================
# LUỒNG 1: BƠM BEACON MỒI ĐA KÊNH CHUẨN WPA2-RSN
# ==========================================
def inject_fake_beacon():
    """
    Bơm khung tin 802.11i Beacon mồi nhử của mạng 'School_Teacher' mỗi INJECT_INTERVAL giây
    xoay vòng trên các kênh hỗ trợ (2.4GHz 1/6/11 + kênh 5GHz AP đang dùng nếu card hỗ trợ).
    """
    logging.info(f"[📡] Beacon Injector chuẩn IEEE 802.11i (RSN WPA2-PSK) khởi chạy trên {BAIT_IFACE}...")
    ch_cursor = 0
    while True:
        try:
            channels = get_candidate_bait_channels(BAIT_IFACE)
            ch = channels[ch_cursor % len(channels)]
            ch_cursor += 1
            _set_iface_channel(BAIT_IFACE, ch)
            frame = build_ieee80211_bait_beacon(ssid=FAKE_SSID, bssid=FAKE_BSSID, channel=ch)
            sendp(frame, iface=BAIT_IFACE, count=5, inter=0.05, verbose=False)
        except Exception as e:
            logging.warning(f"[!] Bơm Beacon mồi tạm ngưng trên {BAIT_IFACE}: {e} (Hệ thống vẫn có Luồng dự phòng SSH/Onboarding)")
        time.sleep(INJECT_INTERVAL)


# ==========================================
# LUỒNG 2: ĐÁNH THỨC & TỈA VAP AN TOÀN (ZERO-DISRUPTION)
# ==========================================
def try_turn_off_teacher_network(target_ip: str):
    """
    Khi hết thời gian đếm ngược:
    - Vùng 'privileged_office': Luôn bật (Always-On), không bao giờ tắt.
    - Vùng 'classroom': Kiểm tra còn GV kết nối không; nếu = 0 mới tắt đúng VAP 'teacher_5g' (0ms ngắt Học sinh).
    """
    if target_ip in PRIVILEGED_OFFICE_IPS:
        return

    logging.info(f"[🔍] Kiểm tra trạng thái '{FAKE_SSID}' tại AP {target_ip} trước khi tỉa VAP...")
    pruned = actuators.prune_unused_ssid(
        target_ip, USER, PWD, vap_section="teacher_5g", disable=True, teacher_ssid=FAKE_SSID
    )

    with ap_lock:
        if target_ip not in ap_status:
            return
        if pruned:
            ap_status[target_ip]["is_woken"] = False
            ap_status[target_ip]["timer"] = None
            logging.info(f"[✂️] GV đã rời khu vực. Đã TẮT VAP '{FAKE_SSID}' trên AP {target_ip} (0ms gián đoạn Học sinh)!")
        else:
            timer = threading.Timer(WAKE_DURATION, try_turn_off_teacher_network, args=[target_ip])
            timer.daemon = True
            timer.start()
            ap_status[target_ip]["timer"] = timer


def wake_up_router(client_mac: str, target_ip: str):
    if not target_ip or target_ip in PRIVILEGED_OFFICE_IPS:
        return
    need_wake = False
    with ap_lock:
        status = ap_status.get(target_ip)
        if not status:
            return
        if not status["is_woken"]:
            status["is_woken"] = True
            need_wake = True
        if status["timer"]:
            status["timer"].cancel()
        timer = threading.Timer(WAKE_DURATION, try_turn_off_teacher_network, args=[target_ip])
        timer.daemon = True
        timer.start()
        status["timer"] = timer

    if need_wake:
        logging.info(f"🚀 PHÁT HIỆN GIÁO VIÊN ({client_mac}) GẦN AP {target_ip}! Đánh thức VAP '{FAKE_SSID}'...")
        ok = actuators.prune_unused_ssid(
            target_ip, USER, PWD, vap_section="teacher_5g", disable=False, teacher_ssid=FAKE_SSID
        )
        if not ok:
            with ap_lock:
                if target_ip in ap_status:
                    ap_status[target_ip]["is_woken"] = False


def extract_probe_ssid(packet) -> Optional[str]:
    """Trích xuất tên SSID trong Directed Probe Request (nếu có)."""
    if not packet.haslayer(Dot11ProbeReq):
        return None
    elt = packet.getlayer(Dot11Elt)
    while elt is not None:
        if getattr(elt, "ID", None) == 0:
            try:
                return bytes(elt.info).decode("utf-8", errors="ignore")
            except Exception:
                return None
        elt = elt.payload.getlayer(Dot11Elt) if hasattr(elt, "payload") else None
    return None


def is_teacher_trigger_packet(packet, vip_macs: Set[str], teacher_ssid: str = FAKE_SSID) -> Optional[str]:
    """
    Nhận diện giáo viên trong CẢ 2 trường hợp:
      1) MAC thật nằm trong danh sách VIP (`vip_macs`), HOẶC
      2) Directed Probe Request hỏi đúng tên mạng `teacher_ssid` (hỗ trợ điện thoại bật Random MAC khi quét).
    Trả về định danh (MAC) nếu khớp, ngược lại trả None.
    """
    if not (packet.haslayer(Dot11ProbeReq) or packet.haslayer(Dot11Auth) or packet.haslayer(Dot11AssoReq)):
        return None
    client_mac = getattr(packet, "addr2", None)
    if not client_mac:
        return None
    mac_up = str(client_mac).upper()
    if mac_up in vip_macs:
        return mac_up
    probe_ssid = extract_probe_ssid(packet)
    if probe_ssid and probe_ssid == teacher_ssid:
        return f"DirectedProbe({mac_up})"
    return None


def process_packet(packet):
    vip_macs = get_vip_macs()
    trigger_id = is_teacher_trigger_packet(packet, vip_macs, teacher_ssid=FAKE_SSID)
    if not trigger_id:
        return

    bssid_receiver = getattr(packet, "addr1", None)
    if bssid_receiver and bssid_receiver.upper() in AP_MAP:
        target_ip = AP_MAP[bssid_receiver.upper()]
        threading.Thread(target=wake_up_router, args=(trigger_id, target_ip), daemon=True).start()
    else:
        with ap_lock:
            classroom_ips = [ip for ip, st in ap_status.items() if st["zone_type"] == "classroom"]
        for ip in classroom_ips:
            threading.Thread(target=wake_up_router, args=(trigger_id, ip), daemon=True).start()


def _check_vip_on_any_ap_interface(ip: str, vip_macs: Set[str]) -> Optional[str]:
    """
    Dự phòng không cần USB Monitor: quét tất cả `iw dev` trên AP xem có MAC VIP giáo viên nào
    vừa kết nối vào mạng phụ/mạng chung hay không -> tự động đánh thức VAP `School_Teacher`.
    """
    if not vip_macs:
        return None
    cmd = (
        "for dev in $(iw dev | awk '$1==\"Interface\"{print $2}'); do "
        "  iw dev \"$dev\" station dump 2>/dev/null | awk '$1==\"Station\"{print toupper($2)}'; "
        "done"
    )
    out = actuators._exec_ssh_output(ip, USER, PWD, cmd)
    for line in out.splitlines():
        mac = line.strip().upper()
        if mac in vip_macs:
            return mac
    return None


def active_presence_monitor_loop():
    """
    Luồng dự phòng đa tầng chạy song song (hoạt động ngay cả khi KHÔNG có USB Wi-Fi Monitor Mode):
      1. Kiểm tra nếu GV đã kết nối vào `School_Teacher` (vd qua Onboarding Portal) -> gắn timer tự tỉa khi rời đi.
      2. Kiểm tra nếu MAC VIP GV xuất hiện trên bất kỳ interface nào của AP phòng học -> tự đánh thức VAP `teacher_5g`.
    """
    while True:
        time.sleep(20)
        vip_macs = get_vip_macs()
        with ap_lock:
            classroom_ips = [ip for ip, st in ap_status.items() if st["zone_type"] == "classroom"]
        for ip in classroom_ips:
            active_macs = actuators.get_teacher_active_clients(ip, USER, PWD, FAKE_SSID)
            if len(active_macs) > 0:
                with ap_lock:
                    st = ap_status.get(ip)
                    if st and st["timer"] is None:
                        st["is_woken"] = True
                        timer = threading.Timer(WAKE_DURATION, try_turn_off_teacher_network, args=[ip])
                        timer.daemon = True
                        timer.start()
                        st["timer"] = timer
                continue

            seen_vip = _check_vip_on_any_ap_interface(ip, vip_macs)
            if seen_vip:
                wake_up_router(seen_vip, ip)


def main():
    load_topology_config()
    if not ap_status:
        logging.critical("[-] File network_config.yaml trống!")
        raise SystemExit(1)

    threading.Thread(target=active_presence_monitor_loop, daemon=True).start()
    threading.Thread(target=inject_fake_beacon, daemon=True).start()

    logging.info(f"[*] Vệ sĩ Mồi nhử & Zero-Disruption VAP Pruning đang lắng nghe trên {IFACE} (Bait={BAIT_IFACE})...")
    try:
        sniff(iface=IFACE, prn=process_packet, store=0)
    except Exception as e:
        logging.warning(f"[!] Chế độ Sniff trên {IFACE} tạm ngưng ({e}). Chuyển sang SSH Presence Monitor dự phòng...")
        while True:
            time.sleep(5)


if __name__ == "__main__":
    main()
