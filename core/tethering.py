"""테더링 IP 변경 모듈 - Wi-Fi 어댑터 제어로 IP 변경

Windows 전용: PowerShell의 Get/Enable/Disable-NetAdapter를 사용하며,
어댑터 비활성화/활성화에는 관리자 권한이 필요하다.
다른 OS에서는 모든 함수가 안전하게 no-op으로 동작한다.
"""

import sys
import subprocess
import time
import re


def _is_windows() -> bool:
    return sys.platform == 'win32'


# Windows에서 PowerShell 실행 시 콘솔 창이 깜빡이는 것 방지
_PS_FLAGS = {}
if sys.platform == 'win32':
    _PS_FLAGS['creationflags'] = 0x08000000  # CREATE_NO_WINDOW


def _run_ps(command: str, timeout: int = 10) -> subprocess.CompletedProcess:
    """PowerShell 명령 실행 래퍼 — 콘솔 창 숨김 + 일관된 인코딩."""
    return subprocess.run(
        ['powershell', '-NoProfile', '-Command', command],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=timeout, **_PS_FLAGS
    )


def get_current_ip() -> str:
    """현재 공인 IP 확인 (PowerShell 의존 — 비Windows에선 '확인 불가')"""
    if not _is_windows():
        return '확인 불가'
    try:
        result = _run_ps('(Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing).Content')
        return result.stdout.strip()
    except Exception:
        return '확인 불가'


def get_wifi_adapter_name() -> str:
    """Wi-Fi 어댑터 이름 가져오기"""
    if not _is_windows():
        return 'Wi-Fi'
    try:
        result = _run_ps(
            'Get-NetAdapter | Where-Object {$_.InterfaceDescription -like "*Wi-Fi*" -or $_.InterfaceDescription -like "*Wireless*" -or $_.Name -like "*Wi-Fi*"} | Select-Object -First 1 -ExpandProperty Name'
        )
        name = result.stdout.strip()
        return name if name else 'Wi-Fi'
    except Exception:
        return 'Wi-Fi'


def get_mobile_adapter_name() -> str:
    """모바일 테더링 어댑터 이름 가져오기"""
    if not _is_windows():
        return ''
    try:
        result = _run_ps(
            'Get-NetAdapter | Where-Object {$_.InterfaceDescription -like "*NDIS*" -or $_.InterfaceDescription -like "*Remote*" -or $_.InterfaceDescription -like "*USB*" -or $_.Name -like "*이더넷*"} | Select-Object -First 1 -ExpandProperty Name'
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

    if not _is_windows():
        log("[테더링] Windows 전용 기능입니다 — 현재 OS에서는 IP 변경을 지원하지 않습니다")
        return False

    old_ip = get_current_ip()
    log(f"[테더링] 현재 IP: {old_ip}")

    adapter_name = get_wifi_adapter_name()
    log(f"[테더링] 어댑터: {adapter_name}")

    try:
        # 어댑터 비활성화 (관리자 권한 필요)
        log("[테더링] 네트워크 어댑터 비활성화...")
        r = _run_ps(f'Disable-NetAdapter -Name "{adapter_name}" -Confirm:$false')
        if r.returncode != 0:
            err = (r.stderr or '').strip()
            log(f"[테더링] 어댑터 비활성화 실패 — 관리자 권한으로 실행했는지 확인하세요. {err}")
            return False

        time.sleep(3)

        # 어댑터 활성화
        log("[테더링] 네트워크 어댑터 활성화...")
        r = _run_ps(f'Enable-NetAdapter -Name "{adapter_name}" -Confirm:$false')
        if r.returncode != 0:
            err = (r.stderr or '').strip()
            log(f"[테더링] 어댑터 활성화 실패 — 수동으로 Wi-Fi를 다시 켜주세요. {err}")
            return False

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

    if not _is_windows():
        log("[비행기모드] Windows 전용 기능입니다 — 현재 OS에서는 IP 변경을 지원하지 않습니다")
        return False

    old_ip = get_current_ip()
    log(f"[비행기모드] 현재 IP: {old_ip}")

    try:
        # 모든 네트워크 어댑터 비활성화 (비행기모드 시뮬레이션, 관리자 권한 필요)
        log("[비행기모드] 네트워크 비활성화...")
        r = _run_ps('Get-NetAdapter | Where-Object {$_.Status -eq "Up"} | Disable-NetAdapter -Confirm:$false')
        if r.returncode != 0:
            err = (r.stderr or '').strip()
            log(f"[비행기모드] 네트워크 비활성화 실패 — 관리자 권한으로 실행했는지 확인하세요. {err}")
            return False

        time.sleep(5)

        # 다시 활성화
        log("[비행기모드] 네트워크 활성화...")
        r = _run_ps('Get-NetAdapter | Where-Object {$_.Status -eq "Disabled"} | Enable-NetAdapter -Confirm:$false')
        if r.returncode != 0:
            err = (r.stderr or '').strip()
            log(f"[비행기모드] 네트워크 활성화 실패 — 수동으로 네트워크를 다시 켜주세요. {err}")
            return False

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
