"""테더링 IP 변경 모듈 - Wi-Fi 어댑터 제어로 IP 변경"""

import subprocess
import time
import re


def get_current_ip() -> str:
    """현재 공인 IP 확인"""
    try:
        result = subprocess.run(
            ['powershell', '-Command', '(Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing).Content'],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except Exception:
        return '확인 불가'


def get_wifi_adapter_name() -> str:
    """Wi-Fi 어댑터 이름 가져오기"""
    try:
        result = subprocess.run(
            ['powershell', '-Command',
             'Get-NetAdapter | Where-Object {$_.InterfaceDescription -like "*Wi-Fi*" -or $_.InterfaceDescription -like "*Wireless*" -or $_.Name -like "*Wi-Fi*"} | Select-Object -First 1 -ExpandProperty Name'],
            capture_output=True, text=True, timeout=10
        )
        name = result.stdout.strip()
        return name if name else 'Wi-Fi'
    except Exception:
        return 'Wi-Fi'


def get_mobile_adapter_name() -> str:
    """모바일 테더링 어댑터 이름 가져오기"""
    try:
        result = subprocess.run(
            ['powershell', '-Command',
             'Get-NetAdapter | Where-Object {$_.InterfaceDescription -like "*NDIS*" -or $_.InterfaceDescription -like "*Remote*" -or $_.InterfaceDescription -like "*USB*" -or $_.Name -like "*이더넷*"} | Select-Object -First 1 -ExpandProperty Name'],
            capture_output=True, text=True, timeout=10
        )
        name = result.stdout.strip()
        return name if name else ''
    except Exception:
        return ''


def toggle_tethering(log_callback=None) -> bool:
    """테더링 연결 재시작으로 IP 변경

    1. Wi-Fi 끄기
    2. 잠시 대기
    3. Wi-Fi 켜기
    4. IP 변경 확인
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    old_ip = get_current_ip()
    log(f"[테더링] 현재 IP: {old_ip}")

    adapter_name = get_wifi_adapter_name()
    log(f"[테더링] 어댑터: {adapter_name}")

    try:
        # 어댑터 비활성화
        log("[테더링] 네트워크 어댑터 비활성화...")
        subprocess.run(
            ['powershell', '-Command', f'Disable-NetAdapter -Name "{adapter_name}" -Confirm:$false'],
            capture_output=True, timeout=10
        )

        time.sleep(3)

        # 어댑터 활성화
        log("[테더링] 네트워크 어댑터 활성화...")
        subprocess.run(
            ['powershell', '-Command', f'Enable-NetAdapter -Name "{adapter_name}" -Confirm:$false'],
            capture_output=True, timeout=10
        )

        # 연결 대기
        log("[테더링] 연결 대기 중...")
        time.sleep(5)

        new_ip = get_current_ip()
        log(f"[테더링] 새 IP: {new_ip}")

        if new_ip != old_ip and new_ip != '확인 불가':
            log("[테더링] IP 변경 성공!")
            return True
        else:
            log("[테더링] IP가 변경되지 않았습니다")
            return False

    except Exception as e:
        log(f"[테더링] 오류: {e}")
        return False


def toggle_airplane_mode(log_callback=None) -> bool:
    """비행기모드 토글로 IP 변경 (모바일 테더링 시)"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    old_ip = get_current_ip()
    log(f"[비행기모드] 현재 IP: {old_ip}")

    try:
        # 모든 네트워크 어댑터 비활성화 (비행기모드 시뮬레이션)
        log("[비행기모드] 네트워크 비활성화...")
        subprocess.run(
            ['powershell', '-Command',
             'Get-NetAdapter | Where-Object {$_.Status -eq "Up"} | Disable-NetAdapter -Confirm:$false'],
            capture_output=True, timeout=10
        )

        time.sleep(5)

        # 다시 활성화
        log("[비행기모드] 네트워크 활성화...")
        subprocess.run(
            ['powershell', '-Command',
             'Get-NetAdapter | Where-Object {$_.Status -eq "Disabled"} | Enable-NetAdapter -Confirm:$false'],
            capture_output=True, timeout=10
        )

        time.sleep(8)

        new_ip = get_current_ip()
        log(f"[비행기모드] 새 IP: {new_ip}")

        if new_ip != old_ip and new_ip != '확인 불가':
            log("[비행기모드] IP 변경 성공!")
            return True
        else:
            log("[비행기모드] IP가 변경되지 않았습니다")
            return False

    except Exception as e:
        log(f"[비행기모드] 오류: {e}")
        return False
