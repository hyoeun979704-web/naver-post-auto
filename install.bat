@echo off
REM ASCII-only batch file. Uses absolute Python path (not 'py' launcher).
title N-Publisher Auto Installer

cd /d "%~dp0"

echo.
echo ===============================================
echo   N-Publisher Auto Installer
echo ===============================================
echo.
echo This script installs:
echo   1. Python 3.12 (auto-downloaded if missing)
echo   2. Required Python packages
echo   3. Chromium browser (for Playwright)
echo   4. Auto-fix config.ini paths
echo.
echo Internet required. Takes 5-15 minutes.
echo.
pause


REM =============================================================
REM  STEP 1: Locate or install Python 3.12 by ABSOLUTE PATH
REM  (do not rely on 'py' launcher - new Windows installs may
REM   not have it or redirect to Microsoft Store)
REM =============================================================
echo.
echo [1/5] Locating Python 3.12...
echo -----------------------------------------------

set "PYEXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%PROGRAMFILES%\Python312\python.exe" set "PYEXE=%PROGRAMFILES%\Python312\python.exe"
if not defined PYEXE if exist "%PROGRAMFILES(x86)%\Python312\python.exe" set "PYEXE=%PROGRAMFILES(x86)%\Python312\python.exe"
if not defined PYEXE if exist "C:\Python312\python.exe" set "PYEXE=C:\Python312\python.exe"

if defined PYEXE (
    echo   Found: %PYEXE%
    "%PYEXE%" --version 2>nul
    if not errorlevel 1 goto python_ready
    echo   [WARN] Found python.exe but failed to run. Will reinstall.
    set "PYEXE="
)

echo   Python 3.12 not found - downloading installer...

REM Download Python 3.12.7 from python.org via PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile 'python-installer.exe' -UseBasicParsing"

if not exist "python-installer.exe" (
    echo.
    echo   [ERROR] Download failed. Check internet connection.
    echo   Manual download:
    echo     https://www.python.org/downloads/release/python-3127/
    pause
    exit /b 1
)

echo   Installing Python 3.12 (silent, 1-2 min)...
python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1 InstallLauncherAllUsers=0
if errorlevel 1 (
    echo   [ERROR] Python install failed. Try running install.bat as Administrator.
    pause
    exit /b 1
)
del python-installer.exe 2>nul

REM Resolve absolute path after install
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%PROGRAMFILES%\Python312\python.exe" set "PYEXE=%PROGRAMFILES%\Python312\python.exe"

if not defined PYEXE (
    echo   [ERROR] Python installed but exe path not found.
    echo   Expected location: %LOCALAPPDATA%\Programs\Python\Python312\python.exe
    pause
    exit /b 1
)

:python_ready
echo   [OK] Python ready: %PYEXE%
"%PYEXE%" --version


REM =============================================================
REM  STEP 2: Upgrade pip
REM =============================================================
echo.
echo [2/5] Upgrading pip...
echo -----------------------------------------------
"%PYEXE%" -m pip install --upgrade pip
if errorlevel 1 (
    echo   [ERROR] pip upgrade failed. Check internet.
    pause
    exit /b 1
)


REM =============================================================
REM  STEP 3: Install Python packages
REM =============================================================
echo.
echo [3/5] Installing Python packages... (5-10 min)
echo -----------------------------------------------
"%PYEXE%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   [ERROR] Package install failed.
    echo   Try running install.bat as Administrator.
    pause
    exit /b 1
)
echo   [OK] Packages installed.


REM =============================================================
REM  STEP 4: Install Chromium for Playwright
REM =============================================================
echo.
echo [4/5] Installing Chromium browser...
echo -----------------------------------------------
"%PYEXE%" -m playwright install chromium
if errorlevel 1 (
    echo.
    echo   [ERROR] Chromium install failed.
    echo   Manual: "%PYEXE%" -m playwright install chromium
    pause
    exit /b 1
)
echo   [OK] Chromium installed.


REM =============================================================
REM  STEP 5: Fix config.ini paths
REM =============================================================
echo.
echo [5/5] Fixing config.ini paths...
echo -----------------------------------------------
if exist "fix_config_paths.py" (
    "%PYEXE%" fix_config_paths.py
) else (
    echo   [SKIP] fix_config_paths.py not found.
)


echo.
echo ===============================================
echo   INSTALL COMPLETE!
echo ===============================================
echo.
echo  Now double-click the launcher file in this folder.
echo  ^(Look for the .pyw file^)
echo.
pause
