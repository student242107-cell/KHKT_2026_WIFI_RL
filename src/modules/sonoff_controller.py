"""
╔══════════════════════════════════════════════════════════════════════╗
║  SONOFF SMART PLUG CONTROLLER — LOCAL HTTP REST API (DIY Mode)     ║
║  Target Hardware: Sonoff S26AI (90-250V AC, Max 2200W/10A)         ║
║──────────────────────────────────────────────────────────────────────║
║  Kết nối: Laptop Edge Server ←→ Mobile Hotspot ←→ Sonoff S26AI    ║
║  Mục đích: Hard Reboot Module cho Dual Fail-Safe Architecture      ║
║                                                                      ║
║  Sonoff S26AI chạy firmware DIY Mode, expose Local HTTP REST API   ║
║  qua mDNS service type: _ewelink._tcp                              ║
║  Endpoint chính: POST http://<IP>:8081/zeroconf/switch             ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os
import time
import json
import logging
import threading
import requests
from typing import Optional, Dict, Any, Tuple

try:
    from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False
    logging.warning(
        "Chưa cài zeroconf. Chạy 'pip install zeroconf' nếu muốn dùng mDNS auto-discovery. "
        "Nếu không, cần cấu hình SONOFF_IP và SONOFF_DEVICE_ID thủ công trong .env"
    )

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("SonoffController")

# =====================================================================
# 1. CẤU HÌNH SONOFF SMART PLUG
# =====================================================================

# IP tĩnh hoặc sẽ được cập nhật bởi mDNS discovery
SONOFF_IP = os.getenv("SONOFF_IP", "192.168.2.250")
SONOFF_PORT = int(os.getenv("SONOFF_PORT", 8081))
SONOFF_DEVICE_ID = os.getenv("SONOFF_DEVICE_ID", "")

# Thời gian chờ giữa OFF → ON khi power cycle (giây)
POWER_CYCLE_DELAY = int(os.getenv("SONOFF_POWER_CYCLE_DELAY", 5))

# Timeout cho HTTP requests tới Sonoff (giây)
SONOFF_HTTP_TIMEOUT = int(os.getenv("SONOFF_HTTP_TIMEOUT", 5))

# Số lần retry tối đa khi gửi lệnh thất bại
SONOFF_MAX_RETRIES = int(os.getenv("SONOFF_MAX_RETRIES", 3))

# Khoảng cách giữa các lần retry (giây)
SONOFF_RETRY_DELAY = float(os.getenv("SONOFF_RETRY_DELAY", 1.0))


# =====================================================================
# 2. mDNS AUTO-DISCOVERY (Tìm Sonoff trên mạng Local tự động)
# =====================================================================

class SonoffMDNSListener:
    """
    Listener cho mDNS service browser.
    Sonoff DIY Mode broadcast service type: _ewelink._tcp.local.
    Khi tìm thấy, tự động cập nhật IP, Port và Device ID.
    """

    def __init__(self):
        self.discovered_devices: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add_service(self, zc: 'Zeroconf', type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info:
            ip = info.parsed_addresses()[0] if info.parsed_addresses() else None
            port = info.port
            # Device ID thường nằm trong tên service hoặc properties
            device_id = info.properties.get(b'id', b'').decode('utf-8', errors='ignore')
            if not device_id:
                # Fallback: parse từ service name (format: eWeLink_<deviceid>._ewelink._tcp.local.)
                device_id = name.split("_")[1].split(".")[0] if "_" in name else ""

            with self._lock:
                self.discovered_devices[device_id] = {
                    "ip": ip,
                    "port": port,
                    "device_id": device_id,
                    "name": name,
                }
            logger.info(
                f"🔍 [mDNS] Phát hiện Sonoff: {name} | "
                f"IP: {ip}:{port} | Device ID: {device_id}"
            )

    def remove_service(self, zc: 'Zeroconf', type_: str, name: str) -> None:
        logger.warning(f"📡 [mDNS] Sonoff ngắt kết nối: {name}")

    def update_service(self, zc: 'Zeroconf', type_: str, name: str) -> None:
        # Re-add để cập nhật thông tin mới nhất
        self.add_service(zc, type_, name)


def discover_sonoff(timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """
    Quét mạng Local bằng mDNS để tìm Sonoff Smart Plug.
    Trả về dict {ip, port, device_id} của thiết bị đầu tiên tìm thấy,
    hoặc None nếu không tìm thấy trong khoảng timeout.
    """
    if not ZEROCONF_AVAILABLE:
        logger.warning(
            "[mDNS] Thư viện zeroconf chưa được cài đặt. "
            "Sử dụng IP tĩnh từ .env thay thế."
        )
        return None

    logger.info(f"🔍 [mDNS] Đang quét mạng Local tìm Sonoff (timeout: {timeout}s)...")
    zc = Zeroconf()
    listener = SonoffMDNSListener()
    browser = ServiceBrowser(zc, "_ewelink._tcp.local.", listener)

    # Chờ đủ timeout hoặc cho đến khi tìm thấy thiết bị
    start = time.time()
    while time.time() - start < timeout:
        with listener._lock:
            if listener.discovered_devices:
                break
        time.sleep(0.5)

    zc.close()

    with listener._lock:
        if listener.discovered_devices:
            first_device = list(listener.discovered_devices.values())[0]
            logger.info(
                f"✅ [mDNS] Tìm thấy Sonoff: "
                f"{first_device['ip']}:{first_device['port']} "
                f"(ID: {first_device['device_id']})"
            )
            return first_device
        else:
            logger.warning(
                f"⚠️ [mDNS] Không tìm thấy Sonoff nào trong {timeout}s. "
                f"Sử dụng cấu hình tĩnh: {SONOFF_IP}:{SONOFF_PORT}"
            )
            return None


# =====================================================================
# 3. SONOFF SMART PLUG CONTROLLER CLASS
# =====================================================================

class SonoffSmartPlug:
    """
    Controller class cho Sonoff Smart Plug S26AI.

    Kiến trúc kết nối out-of-band:
    ┌─────────────┐    Mobile Hotspot     ┌──────────────┐
    │ Laptop Edge │◄─────── WiFi ────────►│ Sonoff S26AI │
    │   Server    │    (192.168.137.x)     │  Smart Plug  │
    └─────────────┘                        └──────┬───────┘
                                                   │ AC 220V
                                            ┌──────┴───────┐
                                            │   Router     │
                                            │ (Adapter)    │
                                            └──────────────┘

    Kết nối qua Mobile Hotspot (192.168.137.x subnet) đảm bảo
    Sonoff vẫn nhận lệnh ngay cả khi router chính bị treo.
    """

    def __init__(
        self,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        device_id: Optional[str] = None,
        auto_discover: bool = True
    ):
        """
        Khởi tạo controller cho Sonoff Smart Plug.

        Args:
            ip: Địa chỉ IP của Sonoff (nếu biết trước).
            port: Port của Sonoff REST API (mặc định 8081).
            device_id: Device ID của Sonoff (cần cho DIY Mode API).
            auto_discover: Nếu True, tự động quét mDNS để tìm Sonoff.
        """
        self.ip = ip or SONOFF_IP
        self.port = port or SONOFF_PORT
        self.device_id = device_id or SONOFF_DEVICE_ID
        self.power_cycle_delay = POWER_CYCLE_DELAY
        self.http_timeout = SONOFF_HTTP_TIMEOUT
        self.max_retries = SONOFF_MAX_RETRIES
        self.retry_delay = SONOFF_RETRY_DELAY

        # Thống kê hoạt động
        self._stats = {
            "total_commands": 0,
            "successful_commands": 0,
            "failed_commands": 0,
            "power_cycles": 0,
            "last_action_time": None,
            "last_error": None,
        }
        self._lock = threading.Lock()

        # Auto-discovery nếu được bật và chưa có device_id
        if auto_discover and not self.device_id:
            discovered = discover_sonoff(timeout=10.0)
            if discovered:
                self.ip = discovered["ip"]
                self.port = discovered["port"]
                self.device_id = discovered["device_id"]

        logger.info(
            f"🔌 SonoffSmartPlug khởi tạo: "
            f"IP={self.ip}:{self.port} | Device ID={self.device_id or '(chưa xác định)'}"
        )

    @property
    def base_url(self) -> str:
        """URL gốc cho API calls."""
        return f"http://{self.ip}:{self.port}"

    @property
    def stats(self) -> Dict[str, Any]:
        """Trả về thống kê hoạt động."""
        with self._lock:
            return dict(self._stats)

    # -----------------------------------------------------------------
    # 3.1: LOW-LEVEL API CALLS
    # -----------------------------------------------------------------

    def _send_request(
        self,
        endpoint: str,
        data: Optional[Dict] = None,
        method: str = "POST"
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Gửi HTTP request đến Sonoff REST API với retry logic.

        Args:
            endpoint: Đường dẫn API (vd: "/zeroconf/switch")
            data: Payload JSON (phần "data" trong request body)
            method: HTTP method (POST/GET)

        Returns:
            Tuple[bool, Optional[Dict]]: (thành_công, response_json)
        """
        url = f"{self.base_url}{endpoint}"
        payload = {
            "deviceid": self.device_id,
            "data": data or {}
        }
        headers = {"Content-Type": "application/json"}

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if method.upper() == "POST":
                    response = requests.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=self.http_timeout
                    )
                else:
                    response = requests.get(
                        url,
                        timeout=self.http_timeout
                    )

                response_json = response.json()

                # Sonoff trả về {"seq": N, "error": 0} khi thành công
                error_code = response_json.get("error", -1)
                if error_code == 0:
                    with self._lock:
                        self._stats["total_commands"] += 1
                        self._stats["successful_commands"] += 1
                        self._stats["last_action_time"] = time.time()
                    return True, response_json
                else:
                    last_error = f"Sonoff API error code: {error_code}"
                    logger.warning(
                        f"⚠️ [Sonoff] Lệnh thất bại (error={error_code}) "
                        f"- Lần thử {attempt}/{self.max_retries}"
                    )

            except requests.exceptions.ConnectTimeout:
                last_error = f"Connection timeout tới {url}"
                logger.warning(
                    f"⏱️ [Sonoff] Timeout kết nối đến {self.ip}:{self.port} "
                    f"- Lần thử {attempt}/{self.max_retries}"
                )
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {e}"
                logger.warning(
                    f"🔌 [Sonoff] Không thể kết nối {self.ip}:{self.port} "
                    f"- Lần thử {attempt}/{self.max_retries}"
                )
            except requests.exceptions.RequestException as e:
                last_error = str(e)
                logger.warning(
                    f"❌ [Sonoff] Request error: {e} "
                    f"- Lần thử {attempt}/{self.max_retries}"
                )
            except json.JSONDecodeError:
                last_error = "Invalid JSON response"
                logger.warning(
                    f"⚠️ [Sonoff] Response không phải JSON hợp lệ "
                    f"- Lần thử {attempt}/{self.max_retries}"
                )

            if attempt < self.max_retries:
                time.sleep(self.retry_delay)

        # Hết số lần retry
        with self._lock:
            self._stats["total_commands"] += 1
            self._stats["failed_commands"] += 1
            self._stats["last_error"] = last_error
        logger.error(f"❌ [Sonoff] Thất bại sau {self.max_retries} lần thử: {last_error}")
        return False, None

    # -----------------------------------------------------------------
    # 3.2: HIGH-LEVEL CONTROL METHODS
    # -----------------------------------------------------------------

    def turn_on(self) -> bool:
        """
        Bật nguồn ổ cắm Sonoff (cấp điện cho Router).

        Returns:
            bool: True nếu thành công.
        """
        logger.info("⚡ [Sonoff] Gửi lệnh BẬT NGUỒN (switch=on)...")
        success, _ = self._send_request("/zeroconf/switch", {"switch": "on"})
        if success:
            logger.info("✅ [Sonoff] Đã BẬT nguồn thành công!")
        else:
            logger.error("❌ [Sonoff] Không thể BẬT nguồn!")
        return success

    def turn_off(self) -> bool:
        """
        Tắt nguồn ổ cắm Sonoff (ngắt điện Router).

        Returns:
            bool: True nếu thành công.
        """
        logger.info("🔌 [Sonoff] Gửi lệnh TẮT NGUỒN (switch=off)...")
        success, _ = self._send_request("/zeroconf/switch", {"switch": "off"})
        if success:
            logger.info("✅ [Sonoff] Đã TẮT nguồn thành công!")
        else:
            logger.error("❌ [Sonoff] Không thể TẮT nguồn!")
        return success

    def get_info(self) -> Optional[Dict]:
        """
        Lấy thông tin trạng thái hiện tại của Sonoff.

        Returns:
            Dict chứa thông tin thiết bị hoặc None nếu thất bại.
        """
        logger.info("📊 [Sonoff] Truy vấn thông tin thiết bị...")
        success, response = self._send_request("/zeroconf/info", {})
        if success and response:
            data = response.get("data", {})
            logger.info(
                f"📊 [Sonoff] Trạng thái: switch={data.get('switch', '?')} | "
                f"RSSI={data.get('signalStrength', '?')}dBm | "
                f"FW={data.get('fwVersion', '?')}"
            )
            return data
        return None

    def power_cycle(self, delay: Optional[int] = None) -> bool:
        """
        Power Cycle: TẮT nguồn → Chờ delay giây → BẬT lại nguồn.
        Đây là thao tác Hard Reboot cho Router.

        Args:
            delay: Thời gian chờ giữa OFF→ON (giây). Mặc định: POWER_CYCLE_DELAY.

        Returns:
            bool: True nếu cả hai bước (OFF và ON) đều thành công.
        """
        delay = delay or self.power_cycle_delay

        logger.info(
            f"🔄 [Sonoff] === BẮT ĐẦU POWER CYCLE === "
            f"(OFF → chờ {delay}s → ON)"
        )

        # Bước 1: Tắt nguồn
        if not self.turn_off():
            logger.error("❌ [Sonoff] Power Cycle THẤT BẠI ở bước TẮT NGUỒN!")
            return False

        # Bước 2: Chờ
        logger.info(f"⏳ [Sonoff] Đang chờ {delay}s trước khi bật lại nguồn...")
        time.sleep(delay)

        # Bước 3: Bật lại nguồn
        if not self.turn_on():
            logger.error(
                "🚨 [Sonoff] Power Cycle THẤT BẠI ở bước BẬT LẠI NGUỒN! "
                "Router có thể đang KHÔNG CÓ ĐIỆN! Cần can thiệp thủ công!"
            )
            return False

        with self._lock:
            self._stats["power_cycles"] += 1

        logger.info("✅ [Sonoff] === POWER CYCLE HOÀN TẤT === Router đang khởi động lại.")
        return True

    def power_cycle_with_pulse(self, pulse_width_ms: int = 5000) -> bool:
        """
        Power Cycle sử dụng tính năng Pulse (Inching) của Sonoff.
        Sonoff tự động BẬT lại sau pulse_width_ms mili-giây.

        Ưu điểm: Chỉ cần 1 request duy nhất, Sonoff tự xử lý timing.
        Nhược điểm: Nếu request thất bại, không có cơ hội retry bật lại.

        Args:
            pulse_width_ms: Thời gian giữ trạng thái OFF (mili-giây).
                           Mặc định: 5000ms (5 giây).

        Returns:
            bool: True nếu lệnh gửi thành công.
        """
        logger.info(
            f"🔄 [Sonoff] Power Cycle qua Pulse Mode "
            f"(OFF → tự BẬT sau {pulse_width_ms}ms)"
        )

        # Bật chế độ Pulse (Inching) trước
        pulse_setup_ok, _ = self._send_request(
            "/zeroconf/pulse",
            {"pulse": "on", "pulseWidth": pulse_width_ms}
        )

        if not pulse_setup_ok:
            logger.warning(
                "⚠️ [Sonoff] Không thể cấu hình Pulse Mode. "
                "Chuyển sang power_cycle thông thường."
            )
            return self.power_cycle(delay=pulse_width_ms // 1000)

        # Gửi lệnh tắt — Sonoff sẽ tự bật lại sau pulse_width_ms
        success = self.turn_off()
        if success:
            with self._lock:
                self._stats["power_cycles"] += 1
            logger.info(
                f"✅ [Sonoff] Pulse Mode đã kích hoạt. "
                f"Router sẽ có điện trở lại sau {pulse_width_ms}ms."
            )
        return success

    # -----------------------------------------------------------------
    # 3.3: HEALTH CHECK & CONNECTIVITY TEST
    # -----------------------------------------------------------------

    def is_reachable(self) -> bool:
        """
        Kiểm tra xem Sonoff có đang online và phản hồi không.

        Returns:
            bool: True nếu Sonoff phản hồi HTTP request.
        """
        try:
            response = requests.post(
                f"{self.base_url}/zeroconf/info",
                json={"deviceid": self.device_id, "data": {}},
                headers={"Content-Type": "application/json"},
                timeout=3
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_switch_state(self) -> Optional[str]:
        """
        Lấy trạng thái ON/OFF hiện tại của ổ cắm.

        Returns:
            str: "on" hoặc "off", hoặc None nếu không lấy được.
        """
        info = self.get_info()
        if info:
            return info.get("switch")
        return None

    def ensure_on(self) -> bool:
        """
        Đảm bảo ổ cắm đang ở trạng thái ON.
        Nếu đang OFF, tự động bật lên.

        Returns:
            bool: True nếu ổ cắm đang ON (hoặc đã bật thành công).
        """
        state = self.get_switch_state()
        if state == "on":
            logger.info("✅ [Sonoff] Ổ cắm đang ON — không cần thay đổi.")
            return True
        elif state == "off":
            logger.warning("⚠️ [Sonoff] Ổ cắm đang OFF! Đang bật lại...")
            return self.turn_on()
        else:
            logger.warning("⚠️ [Sonoff] Không xác định được trạng thái. Thử bật...")
            return self.turn_on()


# =====================================================================
# 4. HÀM TIỆN ÍCH TOÀN CỤC (GLOBAL CONVENIENCE FUNCTIONS)
# =====================================================================

# Singleton instance — dùng chung trong toàn bộ hệ thống
_global_plug: Optional[SonoffSmartPlug] = None
_global_plug_lock = threading.Lock()


def get_sonoff_plug(
    ip: Optional[str] = None,
    port: Optional[int] = None,
    device_id: Optional[str] = None,
    auto_discover: bool = True
) -> SonoffSmartPlug:
    """
    Lấy singleton instance của SonoffSmartPlug.
    Nếu chưa khởi tạo, tạo mới với các tham số được cung cấp.

    Args:
        ip: IP của Sonoff (tuỳ chọn, sẽ dùng .env nếu bỏ trống).
        port: Port API (mặc định 8081).
        device_id: Device ID (tuỳ chọn, sẽ dùng mDNS nếu bỏ trống).
        auto_discover: Có tự động quét mDNS không.

    Returns:
        SonoffSmartPlug: Instance controller.
    """
    global _global_plug
    with _global_plug_lock:
        if _global_plug is None:
            _global_plug = SonoffSmartPlug(
                ip=ip,
                port=port,
                device_id=device_id,
                auto_discover=auto_discover
            )
        return _global_plug


def hard_reboot_router_via_sonoff(
    delay: int = POWER_CYCLE_DELAY,
    use_pulse: bool = False,
    pulse_width_ms: int = 5000
) -> bool:
    """
    Hàm tiện ích để thực hiện Hard Reboot Router qua Sonoff.
    Được gọi từ failsafe_watchdog.py khi phát hiện router treo.

    Args:
        delay: Thời gian OFF trước khi ON lại (giây).
        use_pulse: Sử dụng Pulse Mode thay vì manual cycle.
        pulse_width_ms: Thời gian pulse nếu dùng Pulse Mode.

    Returns:
        bool: True nếu power cycle thành công.
    """
    plug = get_sonoff_plug()

    # Kiểm tra kết nối trước
    if not plug.is_reachable():
        logger.error(
            "🚨 [Hard Reboot] Không thể kết nối đến Sonoff! "
            "Kiểm tra: (1) Sonoff có bật không? "
            "(2) Mobile Hotspot có chạy không? "
            "(3) IP/Port có đúng không?"
        )
        return False

    if use_pulse:
        return plug.power_cycle_with_pulse(pulse_width_ms=pulse_width_ms)
    else:
        return plug.power_cycle(delay=delay)
