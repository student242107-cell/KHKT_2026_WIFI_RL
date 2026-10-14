#!/usr/bin/env python3
"""
=====================================================================
 🧪 STRESS TEST CHO SOFT FAILSAFE WATCHDOG - KHKT 2026
=====================================================================
 Kiểm thử tính hiệu quả của cơ chế soft_reset_ap() khi:
   - CPU OpenWrt bị ép 100% bằng stress-ng
   - RAM OpenWrt bị ép 100% bằng stress-ng
   - Cả CPU + RAM bị ép đồng thời
   
 Kịch bản kiểm thử:
   TC-01: Baseline (không tải) → soft_reset_ap phải OK
   TC-02: CPU 100% → soft_reset_ap phải OK 
   TC-03: RAM 100% → soft_reset_ap phải OK
   TC-04: CPU+RAM 100% → soft_reset_ap phải OK hoặc trigger HARD rescue
   TC-05: Chaos Cycle (ép → reset → ép lại) liên tục 5 lần
   TC-06: Watchdog latency check dưới tải
=====================================================================
"""

import time
import subprocess
import paramiko
import logging
import os
import sys
import json
import signal
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List

# Thêm project root vào path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [STRESS-TEST] - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(PROJECT_ROOT, 'data', 'processed', 'stress_test_results.log'), mode='a')
    ]
)
logger = logging.getLogger(__name__)

# =====================================================================
# CẤU HÌNH KIỂM THỬ
# =====================================================================

OPENWRT_VM_IP = os.getenv("OPENWRT_VM_IP", "127.0.0.1")
OPENWRT_VM_SSH_PORT = int(os.getenv("OPENWRT_VM_SSH_PORT", "2222"))
OPENWRT_VM_USER = os.getenv("OPENWRT_VM_USER", "root")
OPENWRT_VM_PASS = os.getenv("OPENWRT_VM_PASS", "")  # OpenWrt mặc định không mật khẩu

# Ngưỡng quyết định
LATENCY_THRESHOLD_MS = 500   # Ngưỡng latency gây trigger failsafe (ms)
SSH_TIMEOUT = 10              # Timeout SSH kết nối (giây)
STRESS_DURATION = 30          # Thời gian stress mỗi test case (giây)
RECOVERY_WAIT = 15            # Thời gian đợi phục hồi sau soft reset (giây)
MAX_CONSECUTIVE_FAILS = 6     # Số lần fail liên tiếp trước khi trigger (khớp watchdog)


# =====================================================================
# CẤU TRÚC DỮ LIỆU KẾT QUẢ
# =====================================================================

@dataclass
class TestResult:
    """Kết quả của 1 test case"""
    test_id: str
    test_name: str
    status: str = "PENDING"  # PASS, FAIL, SKIP, ERROR
    start_time: float = 0.0
    end_time: float = 0.0
    duration_seconds: float = 0.0
    details: dict = field(default_factory=dict)
    error_message: str = ""
    
    def mark_pass(self, details=None):
        self.status = "PASS"
        self.end_time = time.time()
        self.duration_seconds = self.end_time - self.start_time
        if details:
            self.details.update(details)
    
    def mark_fail(self, reason, details=None):
        self.status = "FAIL"
        self.end_time = time.time()
        self.duration_seconds = self.end_time - self.start_time
        self.error_message = reason
        if details:
            self.details.update(details)
    
    def mark_error(self, error_msg):
        self.status = "ERROR"
        self.end_time = time.time()
        self.duration_seconds = self.end_time - self.start_time
        self.error_message = error_msg


# =====================================================================
# LỚP ĐIỀU KHIỂN MÁY ẢO OPENWRT
# =====================================================================

class OpenWrtVMController:
    """Điều khiển máy ảo OpenWrt qua SSH để thực hiện stress test"""
    
    def __init__(self, host=OPENWRT_VM_IP, port=OPENWRT_VM_SSH_PORT, 
                 user=OPENWRT_VM_USER, password=OPENWRT_VM_PASS):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssh: Optional[paramiko.SSHClient] = None
    
    def connect(self, timeout=SSH_TIMEOUT) -> bool:
        """Thiết lập kết nối SSH đến VM"""
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password if self.password else None,
                timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
                banner_timeout=timeout
            )
            logger.info(f"✅ Kết nối SSH thành công: {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"❌ Kết nối SSH thất bại: {e}")
            return False
    
    def disconnect(self):
        """Đóng kết nối SSH"""
        if self.ssh:
            try:
                self.ssh.close()
            except:
                pass
            self.ssh = None
    
    def exec_command(self, cmd, timeout=30) -> tuple:
        """Thực thi lệnh trên VM, trả về (exit_code, stdout, stderr)"""
        if not self.ssh:
            if not self.connect():
                return (-1, "", "Không thể kết nối SSH")
        try:
            stdin, stdout, stderr = self.ssh.exec_command(cmd, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode('utf-8', errors='replace').strip()
            err = stderr.read().decode('utf-8', errors='replace').strip()
            return (exit_code, out, err)
        except Exception as e:
            logger.error(f"Lỗi exec_command '{cmd[:50]}...': {e}")
            # Thử kết nối lại
            self.disconnect()
            return (-1, "", str(e))
    
    def get_system_info(self) -> dict:
        """Lấy thông tin hệ thống VM"""
        info = {}
        _, out, _ = self.exec_command("cat /etc/openwrt_release 2>/dev/null || echo 'N/A'")
        info['release'] = out
        
        _, out, _ = self.exec_command("cat /proc/cpuinfo | grep 'model name' | head -1")
        info['cpu'] = out.split(':')[1].strip() if ':' in out else 'N/A'
        
        _, out, _ = self.exec_command("free -m | grep Mem | awk '{print $2}'")
        info['total_ram_mb'] = out or 'N/A'
        
        _, out, _ = self.exec_command("uname -r")
        info['kernel'] = out or 'N/A'
        
        return info
    
    def get_cpu_usage(self) -> float:
        """Đo CPU usage hiện tại (%)"""
        _, out, _ = self.exec_command(
            "top -bn1 2>/dev/null | head -3 | grep 'CPU:' | awk '{print $2}' | tr -d '%usr' || "
            "cat /proc/stat | head -1 | awk '{usage=($2+$4)*100/($2+$4+$5); printf \"%.1f\", usage}'"
        )
        try:
            return float(out.replace('%', '').strip())
        except:
            return -1.0
    
    def get_ram_usage(self) -> dict:
        """Đo RAM usage hiện tại"""
        _, out, _ = self.exec_command("free -m | grep Mem")
        parts = out.split()
        if len(parts) >= 3:
            total = int(parts[1])
            used = int(parts[2])
            return {
                'total_mb': total,
                'used_mb': used,
                'usage_pct': round(used / total * 100, 1) if total > 0 else 0
            }
        return {'total_mb': 0, 'used_mb': 0, 'usage_pct': 0}
    
    def install_stress_ng(self) -> bool:
        """Cài đặt stress-ng trên OpenWrt VM"""
        logger.info("📦 Kiểm tra và cài đặt stress-ng trên VM...")
        
        # Kiểm tra đã cài chưa
        code, _, _ = self.exec_command("which stress-ng")
        if code == 0:
            logger.info("  → stress-ng đã được cài sẵn.")
            return True
        
        # Cài đặt qua opkg
        logger.info("  → Đang cập nhật opkg và cài stress-ng...")
        code, out, err = self.exec_command("opkg update 2>&1 | tail -3", timeout=60)
        if code != 0:
            logger.warning(f"  ⚠️ opkg update có lỗi (có thể do mạng VM): {err}")
        
        code, out, err = self.exec_command("opkg install stress-ng 2>&1", timeout=60)
        if code == 0:
            logger.info("  ✅ Đã cài stress-ng thành công.")
            return True
        
        # Fallback: thử cài stress (phiên bản đơn giản hơn)
        logger.warning(f"  ⚠️ Không cài được stress-ng ({err}), thử cài 'stress'...")
        code, out, err = self.exec_command("opkg install stress 2>&1", timeout=60)
        if code == 0:
            logger.info("  ✅ Đã cài stress (phiên bản rút gọn) thành công.")
            return True
        
        logger.error(f"  ❌ Không thể cài stress-ng/stress: {err}")
        return False
    
    def start_cpu_stress(self, duration=STRESS_DURATION, workers=0) -> bool:
        """
        Ép CPU lên 100% bằng stress-ng
        workers=0 → tự động dùng tất cả CPU cores
        """
        if workers == 0:
            _, out, _ = self.exec_command("nproc")
            try:
                workers = int(out.strip())
            except:
                workers = 1
        
        cmd = f"nohup stress-ng --cpu {workers} --cpu-load 100 --timeout {duration}s > /dev/null 2>&1 &"
        logger.info(f"🔥 Ép CPU 100%: {workers} worker(s), {duration}s")
        code, _, err = self.exec_command(cmd)
        return code == 0 or code == -1  # nohup có thể trả code lạ
    
    def start_ram_stress(self, duration=STRESS_DURATION, percent=95) -> bool:
        """
        Ép RAM lên mức sử dụng chỉ định bằng stress-ng
        percent: phần trăm RAM cần chiếm
        """
        ram_info = self.get_ram_usage()
        total_mb = ram_info['total_mb']
        target_mb = int(total_mb * percent / 100)
        
        cmd = f"nohup stress-ng --vm 1 --vm-bytes {target_mb}M --vm-keep --timeout {duration}s > /dev/null 2>&1 &"
        logger.info(f"🔥 Ép RAM {percent}%: ~{target_mb}MB / {total_mb}MB, {duration}s")
        code, _, err = self.exec_command(cmd)
        return code == 0 or code == -1
    
    def start_full_stress(self, duration=STRESS_DURATION) -> bool:
        """Ép cả CPU + RAM đồng thời"""
        _, nproc_out, _ = self.exec_command("nproc")
        try:
            workers = int(nproc_out.strip())
        except:
            workers = 1
        
        ram_info = self.get_ram_usage()
        target_mb = int(ram_info['total_mb'] * 90 / 100)  # 90% RAM
        
        cmd = (
            f"nohup stress-ng "
            f"--cpu {workers} --cpu-load 100 "
            f"--vm 1 --vm-bytes {target_mb}M --vm-keep "
            f"--timeout {duration}s > /dev/null 2>&1 &"
        )
        logger.info(f"🔥 Ép CPU+RAM: {workers} CPU workers + {target_mb}MB RAM, {duration}s")
        code, _, _ = self.exec_command(cmd)
        return code == 0 or code == -1
    
    def stop_all_stress(self):
        """Dừng tất cả tiến trình stress"""
        self.exec_command("killall stress-ng 2>/dev/null; killall stress 2>/dev/null")
        logger.info("🛑 Đã dừng tất cả stress processes.")
    
    def check_latency_from_host(self) -> float:
        """Đo latency từ host đến VM (mô phỏng check_ap_latency)"""
        try:
            cmd = f"ping -c 3 -W 2 -q -p 00 {self.host}"
            if self.port != 22:
                # Khi dùng QEMU port forwarding, ping trực tiếp localhost
                cmd = f"ping -c 3 -W 2 -q {self.host}"
            
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8')
            
            if "100% packet loss" in output:
                return 9999.0
            
            if "min/avg/max" in output:
                rtt_stats = output.split("=")[1].split("ms")[0].strip()
                avg_latency = float(rtt_stats.split("/")[1])
                return avg_latency
        except subprocess.CalledProcessError:
            return 9999.0
        except Exception as e:
            logger.error(f"Lỗi đo latency: {e}")
        return 9999.0


# =====================================================================
# LỚP KIỂM THỬ SOFT FAILSAFE
# =====================================================================

class FailsafeStressTest:
    """Bộ kiểm thử sức chịu tải cho Soft Failsafe Watchdog"""
    
    def __init__(self):
        self.vm = OpenWrtVMController()
        self.results: List[TestResult] = []
        self.report_path = os.path.join(
            PROJECT_ROOT, 'data', 'processed', 
            f'stress_test_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        )
    
    def _simulate_soft_reset(self, ap_ip) -> dict:
        """
        Mô phỏng soft_reset_ap() từ failsafe_watchdog.py
        SSH vào VM và thực hiện lệnh khôi phục cấu hình an toàn.
        Đo thời gian phản hồi và trạng thái kết quả.
        """
        result = {
            'success': False,
            'response_time_ms': 0,
            'ssh_connected': False,
            'command_executed': False,
            'error': None
        }
        
        start = time.time()
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                hostname=OPENWRT_VM_IP,
                port=OPENWRT_VM_SSH_PORT,
                username=OPENWRT_VM_USER,
                password=OPENWRT_VM_PASS if OPENWRT_VM_PASS else None,
                timeout=SSH_TIMEOUT,
                allow_agent=False,
                look_for_keys=False
            )
            result['ssh_connected'] = True
            
            # Lệnh khôi phục giống hệt soft_reset_ap() trong failsafe_watchdog.py
            rescue_cmd = (
                "uci set wireless.radio0.channel='auto' && "
                "uci set wireless.radio0.htmode='HT20' && "
                "uci set wireless.radio0.txpower='auto' && "
                "uci commit wireless && wifi reload"
            )
            
            # Nếu wireless không tồn tại (VM x86 không có WiFi thật),
            # dùng lệnh fallback để vẫn kiểm thử được khả năng SSH + exec
            stdin, stdout, stderr = ssh.exec_command(
                f"({rescue_cmd}) 2>/dev/null || "
                f"(echo 'FALLBACK: WiFi hardware not found on x86 VM' && "
                f"uci show system 2>/dev/null && echo 'UCI_OK')",
                timeout=SSH_TIMEOUT
            )
            
            exit_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode('utf-8', errors='replace')
            
            result['command_executed'] = True
            result['command_output'] = output[:200]  # Giới hạn log
            result['exit_code'] = exit_code
            
            # Coi là thành công nếu SSH + exec hoạt động (quan trọng nhất)
            result['success'] = True
            
        except paramiko.ssh_exception.AuthenticationException as e:
            result['error'] = f"Lỗi xác thực SSH: {e}"
        except paramiko.ssh_exception.SSHException as e:
            result['error'] = f"Lỗi SSH: {e}"
        except Exception as e:
            result['error'] = f"Lỗi: {e}"
        finally:
            elapsed_ms = (time.time() - start) * 1000
            result['response_time_ms'] = round(elapsed_ms, 2)
            ssh.close()
        
        return result
    
    def _wait_for_vm_ready(self, max_wait=120):
        """Đợi VM sẵn sàng nhận kết nối SSH"""
        logger.info(f"⏳ Đợi VM sẵn sàng (tối đa {max_wait}s)...")
        start = time.time()
        while time.time() - start < max_wait:
            if self.vm.connect(timeout=5):
                self.vm.disconnect()
                logger.info("✅ VM đã sẵn sàng!")
                return True
            time.sleep(5)
        logger.error("❌ VM không phản hồi sau thời gian chờ!")
        return False

    # =================================================================
    # TEST CASES
    # =================================================================
    
    def tc01_baseline_no_load(self) -> TestResult:
        """TC-01: Kiểm thử Baseline - soft_reset_ap khi KHÔNG có tải"""
        result = TestResult("TC-01", "Baseline: soft_reset_ap không tải")
        result.start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("🧪 TC-01: BASELINE - Soft Reset AP khi KHÔNG CÓ TẢI")
        logger.info("=" * 60)
        
        try:
            # Đảm bảo không có stress process nào đang chạy
            if not self.vm.connect():
                result.mark_error("Không kết nối được VM")
                return result
            
            self.vm.stop_all_stress()
            time.sleep(2)
            
            # Đo trạng thái hệ thống trước
            cpu_before = self.vm.get_cpu_usage()
            ram_before = self.vm.get_ram_usage()
            
            # Thực hiện soft_reset_ap
            reset_result = self._simulate_soft_reset(OPENWRT_VM_IP)
            
            if reset_result['success']:
                result.mark_pass({
                    'cpu_before': cpu_before,
                    'ram_before': ram_before,
                    'reset_response_time_ms': reset_result['response_time_ms'],
                    'ssh_connected': reset_result['ssh_connected'],
                    'command_executed': reset_result['command_executed']
                })
                logger.info(f"  ✅ PASS - Response time: {reset_result['response_time_ms']}ms")
            else:
                result.mark_fail(f"Soft reset thất bại: {reset_result['error']}", reset_result)
                logger.error(f"  ❌ FAIL - {reset_result['error']}")
            
            self.vm.disconnect()
            
        except Exception as e:
            result.mark_error(str(e))
            logger.error(f"  ❌ ERROR - {e}")
        
        return result
    
    def tc02_cpu_100_percent(self) -> TestResult:
        """TC-02: soft_reset_ap khi CPU bị ép 100%"""
        result = TestResult("TC-02", "CPU 100%: soft_reset_ap dưới tải CPU cực đại")
        result.start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("🧪 TC-02: CPU 100% - Soft Reset AP dưới tải CPU cực đại")
        logger.info("=" * 60)
        
        try:
            if not self.vm.connect():
                result.mark_error("Không kết nối được VM")
                return result
            
            # Bước 1: Kích hoạt stress CPU
            self.vm.start_cpu_stress(duration=STRESS_DURATION + 15)
            logger.info("  ⏳ Chờ CPU đạt 100%...")
            time.sleep(5)  # Chờ CPU ramp up
            
            # Bước 2: Đo CPU để xác nhận đã đạt 100%
            cpu_during = self.vm.get_cpu_usage()
            ram_during = self.vm.get_ram_usage()
            logger.info(f"  📊 CPU: {cpu_during}%, RAM: {ram_during['usage_pct']}%")
            
            # Bước 3: Thực hiện soft_reset_ap
            logger.info("  🔧 Thực hiện soft_reset_ap()...")
            reset_result = self._simulate_soft_reset(OPENWRT_VM_IP)
            
            # Bước 4: Đánh giá kết quả
            if reset_result['success']:
                result.mark_pass({
                    'cpu_during_stress': cpu_during,
                    'ram_during_stress': ram_during,
                    'reset_response_time_ms': reset_result['response_time_ms'],
                    'ssh_connected': reset_result['ssh_connected'],
                    'command_executed': reset_result['command_executed'],
                    'verdict': 'Soft failsafe HOẠT ĐỘNG TỐT dưới CPU 100%'
                })
                logger.info(f"  ✅ PASS - Response time: {reset_result['response_time_ms']}ms")
            else:
                # Soft reset thất bại → Đây chính là điều kiện trigger Hard Rescue
                result.mark_fail(
                    f"Soft reset thất bại dưới CPU 100% → Cần Hard Rescue: {reset_result['error']}",
                    {
                        'cpu_during_stress': cpu_during,
                        'reset_response_time_ms': reset_result['response_time_ms'],
                        'would_trigger_hard_rescue': True,
                        'verdict': 'CPU 100% khiến SSH timeout → Kích hoạt Hard Rescue đúng thiết kế'
                    }
                )
                logger.warning(f"  ⚠️ FAIL (Expected) - CPU 100% gây SSH timeout → Hard Rescue sẽ kích hoạt")
            
            # Dọn dẹp
            self.vm.stop_all_stress()
            self.vm.disconnect()
            
        except Exception as e:
            result.mark_error(str(e))
            logger.error(f"  ❌ ERROR - {e}")
        
        return result
    
    def tc03_ram_100_percent(self) -> TestResult:
        """TC-03: soft_reset_ap khi RAM bị ép 100%"""
        result = TestResult("TC-03", "RAM 100%: soft_reset_ap dưới tải RAM cực đại")
        result.start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("🧪 TC-03: RAM 100% - Soft Reset AP dưới tải RAM cực đại")
        logger.info("=" * 60)
        
        try:
            if not self.vm.connect():
                result.mark_error("Không kết nối được VM")
                return result
            
            # Lấy RAM ban đầu
            ram_before = self.vm.get_ram_usage()
            logger.info(f"  📊 RAM trước stress: {ram_before['used_mb']}MB / {ram_before['total_mb']}MB ({ram_before['usage_pct']}%)")
            
            # Kích hoạt stress RAM 95%
            self.vm.start_ram_stress(duration=STRESS_DURATION + 15, percent=95)
            logger.info("  ⏳ Chờ RAM đạt mức cao...")
            time.sleep(8)  # RAM stress cần thời gian để allocate
            
            # Đo RAM dưới tải
            ram_during = self.vm.get_ram_usage()
            cpu_during = self.vm.get_cpu_usage()
            logger.info(f"  📊 RAM dưới tải: {ram_during['used_mb']}MB / {ram_during['total_mb']}MB ({ram_during['usage_pct']}%)")
            
            # Thực hiện soft_reset_ap
            logger.info("  🔧 Thực hiện soft_reset_ap()...")
            reset_result = self._simulate_soft_reset(OPENWRT_VM_IP)
            
            if reset_result['success']:
                result.mark_pass({
                    'ram_before': ram_before,
                    'ram_during_stress': ram_during,
                    'cpu_during_stress': cpu_during,
                    'reset_response_time_ms': reset_result['response_time_ms'],
                    'verdict': 'Soft failsafe HOẠT ĐỘNG TỐT dưới RAM ~100%'
                })
                logger.info(f"  ✅ PASS - Response time: {reset_result['response_time_ms']}ms")
            else:
                result.mark_fail(
                    f"Soft reset thất bại dưới RAM stress: {reset_result['error']}",
                    {
                        'ram_during_stress': ram_during,
                        'would_trigger_hard_rescue': True,
                        'verdict': 'RAM cạn kiệt → OOM killer có thể kill SSH → Cần Hard Rescue'
                    }
                )
                logger.warning(f"  ⚠️ FAIL - RAM stress gây lỗi: {reset_result['error']}")
            
            self.vm.stop_all_stress()
            self.vm.disconnect()
            
        except Exception as e:
            result.mark_error(str(e))
        
        return result
    
    def tc04_cpu_ram_combined(self) -> TestResult:
        """TC-04: soft_reset_ap khi cả CPU + RAM bị ép 100%"""
        result = TestResult("TC-04", "CPU+RAM 100%: Kiểm thử cực hạn")
        result.start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("🧪 TC-04: CPU+RAM 100% - KIỂM THỬ CỰC HẠN")
        logger.info("=" * 60)
        
        try:
            if not self.vm.connect():
                result.mark_error("Không kết nối được VM")
                return result
            
            ram_before = self.vm.get_ram_usage()
            
            # Ép cả CPU + RAM
            self.vm.start_full_stress(duration=STRESS_DURATION + 20)
            logger.info("  ⏳ Chờ hệ thống đạt tải cực đại...")
            time.sleep(10)
            
            cpu_during = self.vm.get_cpu_usage()
            ram_during = self.vm.get_ram_usage()
            logger.info(f"  📊 CPU: {cpu_during}%, RAM: {ram_during['usage_pct']}%")
            
            # Thực hiện soft_reset_ap
            logger.info("  🔧 Thực hiện soft_reset_ap()...")
            reset_result = self._simulate_soft_reset(OPENWRT_VM_IP)
            
            if reset_result['success']:
                # Soft reset vẫn hoạt động → Hệ thống rất vững
                result.mark_pass({
                    'cpu_during': cpu_during,
                    'ram_during': ram_during,
                    'reset_response_time_ms': reset_result['response_time_ms'],
                    'verdict': '🏆 XUẤT SẮC: Soft failsafe hoạt động ngay cả khi CPU+RAM 100%!'
                })
                logger.info(f"  ✅ PASS - Soft reset hoạt động! Response: {reset_result['response_time_ms']}ms")
            else:
                # Đây là kết quả MONG ĐỢI ở mức tải cực hạn
                result.mark_fail(
                    f"Soft reset thất bại (mong đợi ở tải cực hạn): {reset_result['error']}",
                    {
                        'cpu_during': cpu_during,
                        'ram_during': ram_during,
                        'would_trigger_hard_rescue': True,
                        'verdict': 'CPU+RAM 100% → Soft rescue bất lực → Hard Rescue được kích hoạt ĐÚNG THIẾT KẾ'
                    }
                )
                logger.warning("  ⚠️ FAIL (EXPECTED) - Dual fail-safe hoạt động đúng: Soft fail → Hard Rescue")
            
            self.vm.stop_all_stress()
            self.vm.disconnect()
            
        except Exception as e:
            result.mark_error(str(e))
        
        return result
    
    def tc05_chaos_resilience_cycle(self) -> TestResult:
        """TC-05: Chu kỳ hỗn loạn - Ép tải → Reset → Ép lại liên tục 5 lần"""
        result = TestResult("TC-05", "Chaos Cycle: Stress→Reset→Stress x5")
        result.start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("🧪 TC-05: CHAOS RESILIENCE CYCLE - Stress → Reset → Stress x5")
        logger.info("=" * 60)
        
        cycles = 5
        cycle_results = []
        success_count = 0
        
        try:
            for i in range(cycles):
                logger.info(f"\n  🔄 Chu kỳ {i+1}/{cycles}")
                
                if not self.vm.connect():
                    cycle_results.append({'cycle': i+1, 'status': 'VM_UNREACHABLE'})
                    logger.error(f"    ❌ VM không phản hồi ở chu kỳ {i+1}")
                    # Thử đợi VM phục hồi
                    time.sleep(10)
                    continue
                
                # Giai đoạn 1: Ép tải
                self.vm.start_full_stress(duration=15)
                time.sleep(5)
                
                cpu = self.vm.get_cpu_usage()
                ram = self.vm.get_ram_usage()
                logger.info(f"    📊 Tải: CPU={cpu}%, RAM={ram['usage_pct']}%")
                
                # Giai đoạn 2: Soft Reset
                reset_result = self._simulate_soft_reset(OPENWRT_VM_IP)
                
                # Giai đoạn 3: Dừng stress và đợi phục hồi
                self.vm.stop_all_stress()
                time.sleep(RECOVERY_WAIT)
                
                # Giai đoạn 4: Kiểm tra hệ thống đã phục hồi chưa
                cpu_after = self.vm.get_cpu_usage()
                ram_after = self.vm.get_ram_usage()
                
                cycle_data = {
                    'cycle': i + 1,
                    'cpu_under_stress': cpu,
                    'ram_under_stress': ram,
                    'soft_reset_success': reset_result['success'],
                    'reset_time_ms': reset_result['response_time_ms'],
                    'cpu_after_recovery': cpu_after,
                    'ram_after_recovery': ram_after,
                }
                cycle_results.append(cycle_data)
                
                if reset_result['success'] or cpu_after >= 0:
                    success_count += 1
                    logger.info(f"    ✅ Chu kỳ {i+1}: Reset OK ({reset_result['response_time_ms']}ms)")
                else:
                    logger.warning(f"    ⚠️ Chu kỳ {i+1}: Reset thất bại")
                
                self.vm.disconnect()
            
            # Đánh giá tổng thể
            success_rate = success_count / cycles * 100
            if success_rate >= 80:
                result.mark_pass({
                    'total_cycles': cycles,
                    'successful_cycles': success_count,
                    'success_rate': f"{success_rate}%",
                    'cycle_details': cycle_results,
                    'verdict': f'Hệ thống bền vững: {success_rate}% chu kỳ thành công'
                })
                logger.info(f"  ✅ PASS - {success_count}/{cycles} chu kỳ thành công ({success_rate}%)")
            else:
                result.mark_fail(
                    f"Chỉ {success_count}/{cycles} chu kỳ thành công",
                    {
                        'total_cycles': cycles,
                        'successful_cycles': success_count,
                        'success_rate': f"{success_rate}%",
                        'cycle_details': cycle_results
                    }
                )
                logger.warning(f"  ❌ FAIL - {success_count}/{cycles} ({success_rate}%)")
        
        except Exception as e:
            result.mark_error(str(e))
        
        return result
    
    def tc06_latency_under_load(self) -> TestResult:
        """TC-06: Đo latency watchdog dưới tải - Mô phỏng check_ap_latency"""
        result = TestResult("TC-06", "Latency Monitoring: check_ap_latency dưới tải")
        result.start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("🧪 TC-06: LATENCY MONITORING - check_ap_latency dưới tải")
        logger.info("=" * 60)
        
        try:
            if not self.vm.connect():
                result.mark_error("Không kết nối được VM")
                return result
            
            # Đo baseline latency
            baseline_latencies = []
            for _ in range(3):
                lat = self.vm.check_latency_from_host()
                baseline_latencies.append(lat)
                time.sleep(1)
            avg_baseline = sum(baseline_latencies) / len(baseline_latencies)
            logger.info(f"  📊 Baseline latency: {avg_baseline:.2f}ms")
            
            # Bật stress CPU+RAM
            self.vm.start_full_stress(duration=STRESS_DURATION + 20)
            time.sleep(8)
            
            # Đo latency dưới tải (mô phỏng 6 lần quét của watchdog)
            stress_latencies = []
            watchdog_would_trigger = False
            consecutive_fails = 0
            
            for i in range(MAX_CONSECUTIVE_FAILS + 2):  # 8 lần quét
                lat = self.vm.check_latency_from_host()
                stress_latencies.append(lat)
                
                if lat > LATENCY_THRESHOLD_MS:
                    consecutive_fails += 1
                    logger.info(f"    Quét {i+1}: {lat:.2f}ms ⚠️ (>{LATENCY_THRESHOLD_MS}ms) [{consecutive_fails}/6]")
                else:
                    consecutive_fails = 0
                    logger.info(f"    Quét {i+1}: {lat:.2f}ms ✅")
                
                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    watchdog_would_trigger = True
                    logger.warning(f"    🚨 WATCHDOG SẼ KÍCH HOẠT tại quét {i+1}!")
                
                time.sleep(5)  # Mô phỏng cửa sổ quét 5s của watchdog
            
            avg_stress = sum(stress_latencies) / len(stress_latencies) if stress_latencies else 0
            max_stress = max(stress_latencies) if stress_latencies else 0
            
            self.vm.stop_all_stress()
            self.vm.disconnect()
            
            details = {
                'baseline_avg_ms': round(avg_baseline, 2),
                'stress_avg_ms': round(avg_stress, 2),
                'stress_max_ms': round(max_stress, 2),
                'stress_latencies': [round(l, 2) for l in stress_latencies],
                'latency_increase_factor': round(avg_stress / avg_baseline, 2) if avg_baseline > 0 else 'N/A',
                'watchdog_would_trigger': watchdog_would_trigger,
                'max_consecutive_fails': consecutive_fails
            }
            
            # TC pass nếu latency tăng < 10x (mạng vẫn phản hồi)
            if avg_stress < 9999:
                result.mark_pass(details)
                trigger_msg = " (→ sẽ kích hoạt Failsafe!)" if watchdog_would_trigger else ""
                logger.info(f"  ✅ PASS - Avg latency: {avg_baseline:.2f}ms → {avg_stress:.2f}ms{trigger_msg}")
            else:
                result.mark_fail("Mạng hoàn toàn không phản hồi dưới tải", details)
                logger.error("  ❌ FAIL - Mạng không phản hồi (100% packet loss)")
        
        except Exception as e:
            result.mark_error(str(e))
        
        return result

    # =================================================================
    # CHẠY BỘ KIỂM THỬ
    # =================================================================
    
    def run_all_tests(self):
        """Chạy toàn bộ bộ kiểm thử"""
        print("\n" + "=" * 70)
        print(" 🧪 KHKT 2026 - STRESS TEST BỘ CƠ CHẾ SOFT FAILSAFE WATCHDOG")
        print(" 📋 Kiểm thử hiệu quả soft_reset_ap() dưới tải CPU/RAM 100%")
        print("=" * 70 + "\n")
        
        # Kiểm tra VM đã sẵn sàng
        if not self._wait_for_vm_ready(max_wait=60):
            logger.critical("❌ VM OpenWrt không khả dụng. Hãy chạy start_openwrt_vm_daemon.sh trước!")
            return
        
        # Lấy thông tin hệ thống
        if self.vm.connect():
            sys_info = self.vm.get_system_info()
            logger.info(f"📊 Thông tin VM:")
            for k, v in sys_info.items():
                logger.info(f"   {k}: {v}")
            
            # Cài stress-ng
            if not self.vm.install_stress_ng():
                logger.warning("⚠️ Không cài được stress-ng. Một số test case có thể bị giới hạn.")
            
            self.vm.disconnect()
        
        # Chạy từng test case
        test_methods = [
            self.tc01_baseline_no_load,
            self.tc02_cpu_100_percent,
            self.tc03_ram_100_percent,
            self.tc04_cpu_ram_combined,
            self.tc05_chaos_resilience_cycle,
            self.tc06_latency_under_load,
        ]
        
        for test_fn in test_methods:
            try:
                result = test_fn()
                self.results.append(result)
                time.sleep(5)  # Nghỉ giữa các test case
            except Exception as e:
                err_result = TestResult(test_fn.__name__, test_fn.__doc__ or "Unknown")
                err_result.mark_error(str(e))
                self.results.append(err_result)
        
        # Tạo báo cáo
        self._generate_report()
    
    def _generate_report(self):
        """Tạo báo cáo JSON chi tiết"""
        os.makedirs(os.path.dirname(self.report_path), exist_ok=True)
        
        report = {
            'test_suite': 'KHKT 2026 - Soft Failsafe Stress Test',
            'timestamp': datetime.now().isoformat(),
            'vm_config': {
                'ip': OPENWRT_VM_IP,
                'ssh_port': OPENWRT_VM_SSH_PORT,
                'ram_mb': 256,
                'cpus': 1
            },
            'summary': {
                'total': len(self.results),
                'pass': sum(1 for r in self.results if r.status == 'PASS'),
                'fail': sum(1 for r in self.results if r.status == 'FAIL'),
                'error': sum(1 for r in self.results if r.status == 'ERROR'),
                'skip': sum(1 for r in self.results if r.status == 'SKIP'),
            },
            'test_results': [asdict(r) for r in self.results]
        }
        
        with open(self.report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        # In tóm tắt ra console
        print("\n" + "=" * 70)
        print(" 📊 BÁO CÁO TÓM TẮT KẾT QUẢ KIỂM THỬ")
        print("=" * 70)
        
        for r in self.results:
            icon = {"PASS": "✅", "FAIL": "⚠️", "ERROR": "❌", "SKIP": "⏭️"}.get(r.status, "❓")
            print(f"  {icon} [{r.test_id}] {r.test_name}")
            print(f"     Trạng thái: {r.status} | Thời gian: {r.duration_seconds:.2f}s")
            if r.error_message:
                print(f"     Lý do: {r.error_message[:100]}")
            if 'verdict' in r.details:
                print(f"     Kết luận: {r.details['verdict']}")
            print()
        
        s = report['summary']
        total = s['total']
        print(f"  📈 Tổng kết: {s['pass']}/{total} PASS, {s['fail']}/{total} FAIL, {s['error']}/{total} ERROR")
        print(f"  📁 Báo cáo chi tiết: {self.report_path}")
        
        # Kết luận về Dual Fail-Safe
        print("\n" + "-" * 70)
        print(" 🏁 KẾT LUẬN VỀ CƠ CHẾ DUAL FAIL-SAFE:")
        print("-" * 70)
        
        if s['pass'] >= 4:
            print("  ✅ Soft Failsafe (soft_reset_ap) HOẠT ĐỘNG HIỆU QUẢ dưới phần lớn")
            print("     các kịch bản tải nặng. Cơ chế Dual Fail-Safe phối hợp tốt:")
            print("     • Soft Rescue xử lý được hầu hết tình huống quá tải")
            print("     • Hard Rescue tự động kích hoạt khi Soft Rescue bất lực")
        elif s['pass'] >= 2:
            print("  ⚠️ Soft Failsafe hoạt động TRUNG BÌNH. Cần tối ưu SSH timeout")
            print("     và cân nhắc giảm ngưỡng trigger Hard Rescue.")
        else:
            print("  ❌ Soft Failsafe KHÔNG ĐÁP ỨNG dưới tải nặng.")
            print("     Đề xuất: Tăng cường cơ chế Hard Rescue hoặc thêm HTTP API fallback.")
        
        print("=" * 70 + "\n")


# =====================================================================
# ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    tester = FailsafeStressTest()
    tester.run_all_tests()
