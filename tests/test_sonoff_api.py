"""
╔══════════════════════════════════════════════════════════════════════╗
║  TEST SUITE — SONOFF SMART PLUG S26AI LOCAL HTTP REST API          ║
║──────────────────────────────────────────────────────────────────────║
║  File này dùng để:                                                   ║
║  1. Kiểm tra kết nối laptop ↔ Sonoff qua Mobile Hotspot            ║
║  2. Test các endpoint REST API (switch, info, pulse)                ║
║  3. Mô phỏng Power Cycle flow hoàn chỉnh                           ║
║  4. Kiểm tra mDNS discovery                                         ║
║                                                                      ║
║  Chạy:  python -m pytest tests/test_sonoff_api.py -v                ║
║  Hoặc:  python tests/test_sonoff_api.py  (interactive mode)         ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import sys
import os
import time
import json
import logging
import argparse
import subprocess
import requests

# Thêm project root vào path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [TEST-SONOFF] - %(levelname)s - %(message)s"
)
logger = logging.getLogger("TestSonoff")

# Import module Sonoff Controller
try:
    from src.modules.sonoff_controller import (
        SonoffSmartPlug,
        discover_sonoff,
        get_sonoff_plug,
        hard_reboot_router_via_sonoff,
        SONOFF_IP,
        SONOFF_PORT,
        SONOFF_DEVICE_ID,
    )
    CONTROLLER_IMPORTED = True
except ImportError as e:
    logger.warning(f"Không thể import sonoff_controller: {e}")
    CONTROLLER_IMPORTED = False


# =====================================================================
# 1. KIỂM TRA KẾT NỐI CƠ BẢN (KHÔNG CẦN SONOFF CONTROLLER)
# =====================================================================

def test_ping_sonoff():
    """
    Test 1: Ping trực tiếp đến IP của Sonoff.
    Kiểm tra xem laptop và Sonoff có cùng mạng Mobile Hotspot không.
    """
    sonoff_ip = os.getenv("SONOFF_IP", SONOFF_IP if CONTROLLER_IMPORTED else "192.168.2.250")

    print("\n" + "=" * 60)
    print(f"  🏓 TEST 1: PING ĐẾN SONOFF ({sonoff_ip})")
    print("=" * 60)

    try:
        cmd = f"ping -c 3 -W 2 {sonoff_ip}"
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            # Parse avg latency
            output = result.stdout
            if "min/avg/max" in output:
                avg = output.split("=")[1].split("ms")[0].strip().split("/")[1]
                print(f"  ✅ PASS — Sonoff phản hồi! Latency trung bình: {avg}ms")
            else:
                print(f"  ✅ PASS — Sonoff phản hồi!")
            print(f"  📡 Chi tiết:\n{result.stdout}")
            return True
        else:
            print(f"  ❌ FAIL — Không ping được Sonoff!")
            print(f"  🔍 Kiểm tra:")
            print(f"     1. Mobile Hotspot trên laptop đã bật chưa?")
            print(f"     2. Sonoff đã kết nối vào Mobile Hotspot chưa?")
            print(f"     3. IP {sonoff_ip} có đúng không?")
            print(f"     4. Firewall có chặn ICMP không?")
            if result.stderr:
                print(f"  ⚠️ Error: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        print(f"  ❌ FAIL — Ping timeout (10s)!")
        return False
    except Exception as e:
        print(f"  ❌ FAIL — Lỗi: {e}")
        return False


def test_http_reachable():
    """
    Test 2: Kiểm tra HTTP connectivity đến Sonoff REST API port.
    """
    sonoff_ip = os.getenv("SONOFF_IP", SONOFF_IP if CONTROLLER_IMPORTED else "192.168.2.250")
    sonoff_port = int(os.getenv("SONOFF_PORT", SONOFF_PORT if CONTROLLER_IMPORTED else 8081))
    device_id = os.getenv("SONOFF_DEVICE_ID", SONOFF_DEVICE_ID if CONTROLLER_IMPORTED else "")

    print("\n" + "=" * 60)
    print(f"  🌐 TEST 2: HTTP KẾT NỐI ĐẾN SONOFF API ({sonoff_ip}:{sonoff_port})")
    print("=" * 60)

    url = f"http://{sonoff_ip}:{sonoff_port}/zeroconf/info"
    payload = {
        "deviceid": device_id,
        "data": {}
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5
        )

        print(f"  📥 HTTP Status: {response.status_code}")
        print(f"  📥 Response Body: {response.text}")

        if response.status_code == 200:
            resp_json = response.json()
            error_code = resp_json.get("error", -1)

            if error_code == 0:
                data = resp_json.get("data", {})
                print(f"\n  ✅ PASS — Sonoff API phản hồi thành công!")
                print(f"  ╔══════════════════════════════════════╗")
                print(f"  ║  📊 THÔNG TIN SONOFF SMART PLUG      ║")
                print(f"  ╠══════════════════════════════════════╣")
                print(f"  ║  Switch State : {data.get('switch', 'N/A'):>20} ║")
                print(f"  ║  SSID         : {data.get('ssid', 'N/A'):>20} ║")
                print(f"  ║  RSSI (dBm)   : {str(data.get('signalStrength', 'N/A')):>20} ║")
                print(f"  ║  FW Version   : {data.get('fwVersion', 'N/A'):>20} ║")
                print(f"  ║  Device ID    : {data.get('deviceid', device_id):>20} ║")
                print(f"  ╚══════════════════════════════════════╝")
                return True
            else:
                print(f"  ⚠️ WARN — Sonoff trả lỗi API (error={error_code})")
                print(f"     Có thể Device ID sai hoặc chưa bật DIY Mode.")
                return False
        else:
            print(f"  ❌ FAIL — HTTP {response.status_code}")
            return False

    except requests.exceptions.ConnectTimeout:
        print(f"  ❌ FAIL — Connection Timeout!")
        print(f"  🔍 Sonoff có thể chưa bật DIY Mode hoặc port {sonoff_port} bị chặn.")
        return False
    except requests.exceptions.ConnectionError:
        print(f"  ❌ FAIL — Connection Refused!")
        print(f"  🔍 Kiểm tra: Sonoff có đang chạy DIY Mode trên port {sonoff_port}?")
        return False
    except Exception as e:
        print(f"  ❌ FAIL — Lỗi: {e}")
        return False


# =====================================================================
# 2. TEST SONOFF CONTROLLER CLASS (CẦN IMPORT THÀNH CÔNG)
# =====================================================================

def test_controller_init():
    """
    Test 3: Khởi tạo SonoffSmartPlug controller.
    """
    print("\n" + "=" * 60)
    print(f"  🔧 TEST 3: KHỞI TẠO SONOFF CONTROLLER")
    print("=" * 60)

    if not CONTROLLER_IMPORTED:
        print(f"  ⏭️ SKIP — Không import được sonoff_controller module")
        return None

    try:
        plug = SonoffSmartPlug(auto_discover=False)
        print(f"  ✅ PASS — Controller khởi tạo thành công!")
        print(f"  📌 IP: {plug.ip}:{plug.port}")
        print(f"  📌 Device ID: {plug.device_id or '(chưa cấu hình)'}")
        print(f"  📌 Power Cycle Delay: {plug.power_cycle_delay}s")
        print(f"  📌 HTTP Timeout: {plug.http_timeout}s")
        print(f"  📌 Max Retries: {plug.max_retries}")
        return plug
    except Exception as e:
        print(f"  ❌ FAIL — Lỗi khởi tạo: {e}")
        return None


def test_controller_get_info(plug: 'SonoffSmartPlug'):
    """
    Test 4: Lấy thông tin thiết bị qua Controller.
    """
    print("\n" + "=" * 60)
    print(f"  📊 TEST 4: LẤY THÔNG TIN THIẾT BỊ (get_info)")
    print("=" * 60)

    if plug is None:
        print(f"  ⏭️ SKIP — Controller chưa khởi tạo")
        return False

    try:
        info = plug.get_info()
        if info:
            print(f"  ✅ PASS — Đã lấy thông tin thành công!")
            print(f"  📊 Raw data: {json.dumps(info, indent=2)}")
            return True
        else:
            print(f"  ❌ FAIL — Không lấy được thông tin!")
            return False
    except Exception as e:
        print(f"  ❌ FAIL — Lỗi: {e}")
        return False


def test_controller_switch_state(plug: 'SonoffSmartPlug'):
    """
    Test 5: Đọc trạng thái switch hiện tại.
    """
    print("\n" + "=" * 60)
    print(f"  💡 TEST 5: ĐỌC TRẠNG THÁI SWITCH HIỆN TẠI")
    print("=" * 60)

    if plug is None:
        print(f"  ⏭️ SKIP — Controller chưa khởi tạo")
        return False

    try:
        state = plug.get_switch_state()
        if state:
            emoji = "🟢" if state == "on" else "🔴"
            print(f"  ✅ PASS — Trạng thái hiện tại: {emoji} {state.upper()}")
            return True
        else:
            print(f"  ❌ FAIL — Không đọc được trạng thái!")
            return False
    except Exception as e:
        print(f"  ❌ FAIL — Lỗi: {e}")
        return False


def test_controller_turn_off_on(plug: 'SonoffSmartPlug'):
    """
    Test 6: Test bật/tắt ổ cắm (⚠️ SẼ NGẮT ĐIỆN THẬT!).
    """
    print("\n" + "=" * 60)
    print(f"  ⚡ TEST 6: BẬT / TẮT Ổ CẮM (LIVE TEST)")
    print(f"  ⚠️  CẢNH BÁO: Test này sẽ TẮT rồi BẬT lại ổ cắm thật!")
    print("=" * 60)

    if plug is None:
        print(f"  ⏭️ SKIP — Controller chưa khởi tạo")
        return False

    try:
        # Ghi nhận trạng thái ban đầu
        original_state = plug.get_switch_state()
        print(f"  📌 Trạng thái ban đầu: {original_state}")

        # Test TẮT
        print(f"\n  🔌 Đang TẮT ổ cắm...")
        off_ok = plug.turn_off()
        if not off_ok:
            print(f"  ❌ FAIL — Không thể tắt!")
            return False
        print(f"  ✅ Đã tắt thành công!")

        # Chờ 2 giây
        print(f"  ⏳ Chờ 2 giây...")
        time.sleep(2)

        # Test BẬT
        print(f"  ⚡ Đang BẬT lại ổ cắm...")
        on_ok = plug.turn_on()
        if not on_ok:
            print(f"  ❌ FAIL — Không thể bật lại!")
            print(f"  🚨 NGUY HIỂM: Router có thể đang KHÔNG CÓ ĐIỆN!")
            return False
        print(f"  ✅ Đã bật lại thành công!")

        # Xác nhận trạng thái cuối
        time.sleep(1)
        final_state = plug.get_switch_state()
        print(f"  📌 Trạng thái cuối: {final_state}")

        if final_state == "on":
            print(f"\n  ✅ PASS — Test bật/tắt hoàn tất thành công!")
            return True
        else:
            print(f"\n  ⚠️ WARN — Trạng thái cuối không phải ON: {final_state}")
            return False

    except Exception as e:
        print(f"  ❌ FAIL — Lỗi: {e}")
        # Cố gắng bật lại để an toàn
        try:
            plug.turn_on()
        except Exception:
            pass
        return False


def test_power_cycle_simulation(plug: 'SonoffSmartPlug'):
    """
    Test 7: Mô phỏng Power Cycle hoàn chỉnh (⚠️ Router sẽ bị reboot!).
    """
    print("\n" + "=" * 60)
    print(f"  🔄 TEST 7: POWER CYCLE MÔ PHỎNG")
    print(f"  ⚠️  CẢNH BÁO: Test này sẽ NGẮT ĐIỆN ROUTER trong 5 giây!")
    print("=" * 60)

    if plug is None:
        print(f"  ⏭️ SKIP — Controller chưa khởi tạo")
        return False

    confirm = input("\n  ❓ Bạn có muốn tiếp tục? Router sẽ bị tắt 5 giây! (y/N): ")
    if confirm.lower() != 'y':
        print(f"  ⏭️ SKIP — Người dùng hủy bỏ.")
        return None

    try:
        print(f"\n  🔄 Bắt đầu Power Cycle (OFF → 5s → ON)...")
        success = plug.power_cycle(delay=5)

        if success:
            print(f"\n  ✅ PASS — Power Cycle hoàn tất!")
            print(f"  ⏳ Router đang khởi động lại, cần ~30-60s để hoạt động bình thường.")

            # Thống kê
            stats = plug.stats
            print(f"\n  📊 Thống kê:")
            print(f"     Total commands: {stats['total_commands']}")
            print(f"     Successful: {stats['successful_commands']}")
            print(f"     Failed: {stats['failed_commands']}")
            print(f"     Power cycles: {stats['power_cycles']}")
            return True
        else:
            print(f"\n  ❌ FAIL — Power Cycle thất bại!")
            return False

    except Exception as e:
        print(f"  ❌ FAIL — Lỗi: {e}")
        return False


# =====================================================================
# 3. TEST mDNS DISCOVERY
# =====================================================================

def test_mdns_discovery():
    """
    Test 8: Quét mDNS để tìm Sonoff trên mạng Local.
    """
    print("\n" + "=" * 60)
    print(f"  🔍 TEST 8: mDNS AUTO-DISCOVERY")
    print("=" * 60)

    if not CONTROLLER_IMPORTED:
        print(f"  ⏭️ SKIP — Không import được sonoff_controller")
        return None

    try:
        print(f"  🔍 Đang quét mạng... (timeout: 10s)")
        result = discover_sonoff(timeout=10.0)

        if result:
            print(f"\n  ✅ PASS — Tìm thấy Sonoff qua mDNS!")
            print(f"  📌 IP: {result.get('ip')}")
            print(f"  📌 Port: {result.get('port')}")
            print(f"  📌 Device ID: {result.get('device_id')}")
            print(f"  📌 Service Name: {result.get('name')}")
            return True
        else:
            print(f"\n  ⚠️ WARN — Không tìm thấy Sonoff qua mDNS.")
            print(f"  🔍 Có thể do:")
            print(f"     1. Sonoff chưa bật DIY Mode")
            print(f"     2. Firewall chặn mDNS (port 5353)")
            print(f"     3. Sonoff và laptop khác subnet")
            return False

    except Exception as e:
        print(f"  ❌ FAIL — Lỗi: {e}")
        return False


# =====================================================================
# 4. TEST TÍCH HỢP WATCHDOG (Mô phỏng kịch bản router treo)
# =====================================================================

def test_router_hang_detection():
    """
    Test 9: Mô phỏng kịch bản phát hiện router treo.
    Kiểm tra logic: ping router → nếu latency > 10s → trigger power cycle.
    """
    router_ip = os.getenv("MASTER_ROUTER_IP", "192.168.2.1")

    print("\n" + "=" * 60)
    print(f"  🏥 TEST 9: MÔ PHỎNG PHÁT HIỆN ROUTER TREO")
    print(f"  📌 Router IP: {router_ip}")
    print("=" * 60)

    # Bước 1: Ping router để đo latency thực tế
    print(f"\n  📡 Bước 1: Đo latency hiện tại của router...")
    try:
        cmd = f"ping -c 5 -W 3 {router_ip}"
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=20
        )

        if result.returncode == 0 and "min/avg/max" in result.stdout:
            rtt_stats = result.stdout.split("=")[1].split("ms")[0].strip()
            avg_latency = float(rtt_stats.split("/")[1])
            max_latency = float(rtt_stats.split("/")[2])

            print(f"  📊 Latency trung bình: {avg_latency:.1f}ms")
            print(f"  📊 Latency cao nhất : {max_latency:.1f}ms")

            if avg_latency > 10000:  # > 10 giây
                print(f"\n  🚨 CẢNH BÁO: Latency vượt ngưỡng 10s!")
                print(f"     → Đây là kịch bản cần trigger Hard Reboot!")
                print(f"     → Trong production, failsafe_watchdog sẽ gọi power_cycle()")
            elif avg_latency > 500:
                print(f"\n  ⚠️ Latency cao ({avg_latency:.1f}ms > 500ms)")
                print(f"     → Trong production, Soft Rescue sẽ được kích hoạt trước.")
            else:
                print(f"\n  ✅ Router hoạt động bình thường!")
                print(f"     → Không cần can thiệp.")

        elif result.returncode != 0:
            loss_pct = "100%"
            if "packet loss" in result.stdout:
                loss_str = result.stdout.split("packet loss")[0].split(",")[-1].strip()
                loss_pct = loss_str

            print(f"  🚨 Router KHÔNG PHẢN HỒI! Packet loss: {loss_pct}")
            print(f"     → Đây chính là kịch bản Hard Reboot Module cần xử lý!")

        return True

    except subprocess.TimeoutExpired:
        print(f"  🚨 Ping TIMEOUT (20s) — Router hoàn toàn không phản hồi!")
        print(f"     → Hard Reboot Module sẽ tự động power cycle qua Sonoff.")
        return True  # Test vẫn pass — chỉ là mô phỏng
    except Exception as e:
        print(f"  ❌ FAIL — Lỗi: {e}")
        return False


# =====================================================================
# 5. TEST TỔ HỢP: END-TO-END INTEGRATION
# =====================================================================

def test_full_integration():
    """
    Test 10: Kiểm tra toàn bộ flow tích hợp:
    1. Ping Sonoff ✓
    2. HTTP API ✓
    3. Get info ✓
    4. Đọc switch state ✓
    5. Ensure ON ✓
    """
    print("\n" + "=" * 60)
    print(f"  🔗 TEST 10: FULL INTEGRATION TEST (KHÔNG POWER CYCLE)")
    print("=" * 60)

    if not CONTROLLER_IMPORTED:
        print(f"  ⏭️ SKIP — Không import được sonoff_controller")
        return None

    results = {}

    # Sub-test 1: Ping
    print(f"\n  [1/5] Ping Sonoff...")
    results["ping"] = test_ping_sonoff()

    if not results["ping"]:
        print(f"\n  ❌ Integration test dừng lại — Sonoff không phản hồi ping.")
        return False

    # Sub-test 2: HTTP
    print(f"\n  [2/5] HTTP API...")
    results["http"] = test_http_reachable()

    if not results["http"]:
        print(f"\n  ❌ Integration test dừng lại — HTTP API không phản hồi.")
        return False

    # Sub-test 3: Controller init
    plug = SonoffSmartPlug(auto_discover=False)

    # Sub-test 4: Get info
    print(f"\n  [3/5] Get device info...")
    info = plug.get_info()
    results["info"] = info is not None
    print(f"  {'✅' if results['info'] else '❌'} Get info: {'OK' if results['info'] else 'FAIL'}")

    # Sub-test 5: Switch state
    print(f"\n  [4/5] Read switch state...")
    state = plug.get_switch_state()
    results["state"] = state is not None
    print(f"  {'✅' if results['state'] else '❌'} Switch state: {state or 'FAIL'}")

    # Sub-test 6: Ensure ON
    print(f"\n  [5/5] Ensure switch is ON...")
    results["ensure_on"] = plug.ensure_on()
    print(f"  {'✅' if results['ensure_on'] else '❌'} Ensure ON: {'OK' if results['ensure_on'] else 'FAIL'}")

    # Tổng kết
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n  {'=' * 40}")
    print(f"  📊 KẾT QUẢ: {passed}/{total} tests passed")
    print(f"  {'=' * 40}")

    for test_name, result in results.items():
        emoji = "✅" if result else "❌"
        print(f"     {emoji} {test_name}")

    return passed == total


# =====================================================================
# 6. INTERACTIVE CLI
# =====================================================================

def interactive_menu():
    """Menu tương tác để chạy từng test thủ công."""

    print("\n")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║       SONOFF S26AI — TEST SUITE & DIAGNOSTIC TOOL          ║")
    print("║       Local HTTP REST API (DIY Mode) Controller            ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║                                                              ║")
    print("║   [1] 🏓 Ping Sonoff                                        ║")
    print("║   [2] 🌐 Test HTTP API connectivity                         ║")
    print("║   [3] 🔧 Khởi tạo Controller                                ║")
    print("║   [4] 📊 Lấy thông tin thiết bị (get_info)                  ║")
    print("║   [5] 💡 Đọc trạng thái switch                              ║")
    print("║   [6] ⚡ Test bật/tắt (LIVE! Sẽ ngắt điện 2s)              ║")
    print("║   [7] 🔄 Power Cycle (LIVE! Router reboot 5s)               ║")
    print("║   [8] 🔍 mDNS Auto-Discovery                                ║")
    print("║   [9] 🏥 Mô phỏng phát hiện router treo                     ║")
    print("║  [10] 🔗 Full Integration Test (an toàn)                     ║")
    print("║   [A] 🚀 Chạy TẤT CẢ tests an toàn (1-5, 8-10)            ║")
    print("║   [Q] 🚪 Thoát                                              ║")
    print("║                                                              ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    plug = None

    while True:
        choice = input("\n  Chọn test [1-10, A, Q]: ").strip().upper()

        if choice == "Q":
            print("\n  👋 Thoát. Bye!")
            break
        elif choice == "1":
            test_ping_sonoff()
        elif choice == "2":
            test_http_reachable()
        elif choice == "3":
            plug = test_controller_init()
        elif choice == "4":
            if plug is None:
                plug = test_controller_init()
            test_controller_get_info(plug)
        elif choice == "5":
            if plug is None:
                plug = test_controller_init()
            test_controller_switch_state(plug)
        elif choice == "6":
            if plug is None:
                plug = test_controller_init()
            confirm = input("  ⚠️ Router sẽ mất điện 2 giây! Tiếp tục? (y/N): ")
            if confirm.lower() == 'y':
                test_controller_turn_off_on(plug)
            else:
                print("  ⏭️ Hủy bỏ.")
        elif choice == "7":
            if plug is None:
                plug = test_controller_init()
            test_power_cycle_simulation(plug)
        elif choice == "8":
            test_mdns_discovery()
        elif choice == "9":
            test_router_hang_detection()
        elif choice == "10":
            test_full_integration()
        elif choice == "A":
            print("\n  🚀 CHẠY TẤT CẢ TESTS AN TOÀN...")
            results = {}
            results["1. Ping"] = test_ping_sonoff()
            results["2. HTTP API"] = test_http_reachable()
            plug = test_controller_init()
            results["3. Controller Init"] = plug is not None
            if plug:
                results["4. Get Info"] = test_controller_get_info(plug)
                results["5. Switch State"] = test_controller_switch_state(plug)
            results["8. mDNS Discovery"] = test_mdns_discovery()
            results["9. Router Hang Sim"] = test_router_hang_detection()

            print("\n" + "=" * 60)
            print("  📊 TỔNG KẾT TẤT CẢ TESTS")
            print("=" * 60)
            for name, result in results.items():
                if result is None:
                    emoji = "⏭️"
                    status = "SKIPPED"
                elif result:
                    emoji = "✅"
                    status = "PASSED"
                else:
                    emoji = "❌"
                    status = "FAILED"
                print(f"  {emoji} {name}: {status}")

            passed = sum(1 for v in results.values() if v is True)
            skipped = sum(1 for v in results.values() if v is None)
            failed = sum(1 for v in results.values() if v is False)
            print(f"\n  📊 Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
        else:
            print("  ⚠️ Lựa chọn không hợp lệ. Chọn 1-10, A, hoặc Q.")


# =====================================================================
# ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sonoff S26AI Smart Plug — Test Suite & Diagnostic Tool"
    )
    parser.add_argument(
        "--test", type=str, default="interactive",
        choices=[
            "interactive", "ping", "http", "info", "state",
            "switch", "cycle", "mdns", "router", "integration", "all"
        ],
        help="Chọn test để chạy (mặc định: interactive menu)"
    )
    parser.add_argument(
        "--ip", type=str, default=None,
        help="Override IP của Sonoff (mặc định: đọc từ .env)"
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="Override port của Sonoff (mặc định: 8081)"
    )

    args = parser.parse_args()

    # Override IP/Port nếu có
    if args.ip:
        os.environ["SONOFF_IP"] = args.ip
    if args.port:
        os.environ["SONOFF_PORT"] = str(args.port)

    if args.test == "interactive":
        interactive_menu()
    elif args.test == "ping":
        test_ping_sonoff()
    elif args.test == "http":
        test_http_reachable()
    elif args.test == "info":
        plug = SonoffSmartPlug(auto_discover=False) if CONTROLLER_IMPORTED else None
        test_controller_get_info(plug)
    elif args.test == "state":
        plug = SonoffSmartPlug(auto_discover=False) if CONTROLLER_IMPORTED else None
        test_controller_switch_state(plug)
    elif args.test == "switch":
        plug = SonoffSmartPlug(auto_discover=False) if CONTROLLER_IMPORTED else None
        test_controller_turn_off_on(plug)
    elif args.test == "cycle":
        plug = SonoffSmartPlug(auto_discover=False) if CONTROLLER_IMPORTED else None
        test_power_cycle_simulation(plug)
    elif args.test == "mdns":
        test_mdns_discovery()
    elif args.test == "router":
        test_router_hang_detection()
    elif args.test == "integration":
        test_full_integration()
    elif args.test == "all":
        # Chạy tất cả tests an toàn (không power cycle)
        test_ping_sonoff()
        test_http_reachable()
        plug = test_controller_init()
        if plug:
            test_controller_get_info(plug)
            test_controller_switch_state(plug)
        test_mdns_discovery()
        test_router_hang_detection()
        test_full_integration()
