"""N통합 발행기 런처 — 콘솔 없이 실행

의존성(PySide6/playwright/anthropic)이 설치된 Python 3.12를 명시적으로 사용한다.
사용자가 새 파이썬(3.13/3.14 등)을 설치해도 의존성 없는 새 인터프리터로 떨어지지 않게
다음 우선순위로 인터프리터를 선택:
  1) `py -3.12` 로 찾은 Python 3.12
  2) 알려진 표준 설치 경로
  3) 현재 인터프리터(폴백)

처음 실행 시 의존성이 없으면 Win32 메시지 박스로 설치 안내.
"""
import os
import sys
import shutil
import subprocess
import ctypes


def _find_python_312() -> str:
    """PySide6 등이 깔린 Python 3.12 의 python.exe 경로를 반환.
    절대경로 우선 — 새 Windows에서 'py' launcher가 Microsoft Store로 리다이렉트되거나
    "No runtime installed" 오류 내는 경우가 있어서 직접 경로를 먼저 본다."""
    candidates = [
        os.path.expandvars(r'%LOCALAPPDATA%\Programs\Python\Python312\python.exe'),
        os.path.expandvars(r'%PROGRAMFILES%\Python312\python.exe'),
        os.path.expandvars(r'%PROGRAMFILES(X86)%\Python312\python.exe'),
        r'C:\Python312\python.exe',
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c

    # Fallback: py launcher
    py = shutil.which('py')
    if py:
        try:
            r = subprocess.run(
                [py, '-3.12', '-c', 'import sys; print(sys.executable)'],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000)
            path = (r.stdout or '').strip()
            if r.returncode == 0 and path and os.path.isfile(path):
                return path
        except Exception:
            pass

    return sys.executable


def _msgbox(title: str, text: str, style: int = 0x10):
    """Win32 MessageBox — PySide6 없이도 동작 (ctypes 표준 lib)."""
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, style)
    except Exception:
        pass


def _check_deps(py_path: str) -> tuple:
    """필수 모듈을 import 시도. (성공 여부, 누락 모듈 리스트)."""
    test_code = (
        "import sys, importlib\n"
        "missing = []\n"
        "for mod in ['PySide6', 'playwright', 'anthropic', 'PIL', 'requests', 'bs4']:\n"
        "    try: importlib.import_module(mod)\n"
        "    except ImportError: missing.append(mod)\n"
        "print('|'.join(missing))\n"
    )
    try:
        r = subprocess.run(
            [py_path, '-c', test_code],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000)
        if r.returncode != 0:
            return False, ['(인터프리터 실행 실패)']
        missing = [m for m in (r.stdout or '').strip().split('|') if m]
        return (len(missing) == 0), missing
    except Exception as e:
        return False, [f'(체크 실패: {e})']


base = os.path.dirname(os.path.abspath(__file__))
py_path = _find_python_312()

# 의존성 사전 체크
ok, missing = _check_deps(py_path)
if not ok:
    # 영어 파일명 우선, 한글 파일명도 호환
    install_bat = None
    for cand in ('install.bat', '처음실행_설치.bat'):
        p = os.path.join(base, cand)
        if os.path.isfile(p):
            install_bat = p
            break
    if install_bat:
        _msgbox(
            "N통합 발행기 — 처음 실행 안내",
            "필요한 패키지가 설치되지 않았습니다.\n\n"
            f"누락: {', '.join(missing)}\n\n"
            "확인을 누르면 자동 설치가 시작됩니다.\n"
            "(인터넷 연결 필요, 5~15분 소요)\n"
            "Python 3.12도 자동 설치됩니다.\n\n"
            "설치 완료 후 N통합발행기 를 다시 더블클릭하세요.",
            0x40,  # Information icon
        )
        try:
            subprocess.Popen(['cmd', '/c', 'start', '', install_bat], cwd=base, shell=False)
        except Exception:
            os.startfile(install_bat)
    else:
        _msgbox(
            "N통합 발행기 — 의존성 누락",
            "Python 3.12 또는 필요한 패키지가 설치되지 않았습니다.\n\n"
            f"누락: {', '.join(missing)}\n\n"
            "install.bat을 더블클릭하거나, 수동으로:\n"
            "  py -3.12 -m pip install -r requirements.txt\n"
            "  py -3.12 -m playwright install chromium",
            0x10,  # Error icon
        )
    sys.exit(0)

# python.exe → pythonw.exe (콘솔 창 안 뜨게)
pyw_path = py_path
if pyw_path.lower().endswith('python.exe'):
    candidate = pyw_path[:-len('python.exe')] + 'pythonw.exe'
    if os.path.isfile(candidate):
        pyw_path = candidate

subprocess.Popen(
    [pyw_path, os.path.join(base, 'cafe_poster_qt.py')],
    cwd=base,
    creationflags=0x08000000,  # CREATE_NO_WINDOW
)
