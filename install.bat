@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul
title NeonRoute Installation and Setup
cd /d "%~dp0"

echo =======================================================
echo          NeonRoute - Installation and Setup
echo =======================================================
echo.

:: 1. Priority 1: Check for autonomous embedded Python runtime
if exist "runtime\python\python.exe" (
    set "PYTHON_BIN=runtime\python\python.exe"
    set "IS_EMBEDDED=1"
    echo [OK] Autonomous embedded Python runtime detected in .\runtime\python\
    goto :python_ready
)

:: 2. Priority 2: Fallback to searching system Python 3.10+
set "PYTHON_EXE="
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python") do (
    %%~C -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 and sys.version_info[1] >= 10 else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PYTHON_EXE=%%~C"
        goto :found_python
    )
)

:found_python
if not defined PYTHON_EXE (
    echo [ERROR] Compatible Python 3.10+ interpreter not found.
    echo Please install Python 3.10 or higher from https://www.python.org/
    echo Make sure to check Add Python to PATH during installation.
    echo =======================================================
    if not "%1"=="--unattended" if not defined CI pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PYTHON_EXE% --version 2^>^&1') do (
    echo [OK] Found system Python interpreter: %%v
)

:: 3. Create virtual environment for system Python fallback
if exist "venv\Scripts\python.exe" (
    echo.
    echo [1/5] Virtual environment .\venv already exists. Updating packages...
) else (
    echo.
    echo [1/5] Creating virtual environment in .\venv ...
    %PYTHON_EXE% -m venv venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        if not "%1"=="--unattended" if not defined CI pause
        exit /b !ERRORLEVEL!
    )
    echo [OK] Virtual environment created successfully.
)
set "PYTHON_BIN=venv\Scripts\python.exe"

:python_ready
for /f "tokens=*" %%v in ('"%PYTHON_BIN%" --version 2^>^&1') do (
    echo [OK] Active Python interpreter: %%v
)

:: 4. Check / Upgrade pip
echo.
echo [2/5] Checking and upgrading pip...
if exist "wheels\" (
    "%PYTHON_BIN%" -m pip install --no-index --find-links=wheels --upgrade pip 2>nul
) else (
    "%PYTHON_BIN%" -m pip install --default-timeout 60 --retries 3 --upgrade pip 2>nul
)
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Pip upgrade skipped or warning encountered, continuing...
)

:: 5. Install dependencies from requirements.txt
echo.
echo [3/5] Installing dependencies from requirements.txt...
set "SKIP_PIP="
if defined IS_EMBEDDED (
    "%PYTHON_BIN%" -c "import PyQt6, qfluentwidgets, fastapi, lancedb" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        echo [OK] Pre-installed dependencies found in autonomous runtime. Skipping pip install.
        set "SKIP_PIP=1"
    )
)

if not defined SKIP_PIP (
    if exist "wheels\" (
        echo [OFFLINE] Found wheels directory. Installing from local cache...
        "%PYTHON_BIN%" -m pip install --no-index --find-links=wheels -r requirements.txt
    ) else (
        echo [ONLINE] Installing dependencies from PyPI...
        "%PYTHON_BIN%" -m pip install --default-timeout 60 --retries 3 -r requirements.txt
    )

    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo [ERROR] Package installation failed.
        echo Please check network connection or wheels integrity and review the log above.
        echo =======================================================
        if not "%1"=="--unattended" if not defined CI pause
        exit /b !ERRORLEVEL!
    )
)

:: 6. Create application shortcuts
echo.
echo [4/5] Creating application shortcuts...
set "SHORTCUT_ARGS="
if "%1"=="--unattended" set "SHORTCUT_ARGS=--no-desktop"
if "%2"=="--unattended" set "SHORTCUT_ARGS=--no-desktop"
"%PYTHON_BIN%" scripts\create_shortcuts.py %SHORTCUT_ARGS%
if !ERRORLEVEL! NEQ 0 (
    echo [WARNING] Shortcut creation skipped or failed, continuing...
)

:: 7. Check and download llama-server binary
echo.
echo [5/5] Checking llama-server engine...
set "SKIP_LLAMA="
if "%1"=="--skip-llama" set "SKIP_LLAMA=1"
if "%2"=="--skip-llama" set "SKIP_LLAMA=1"
if defined NEONROUTE_SKIP_LLAMA set "SKIP_LLAMA=1"
if exist "wheels\" set "SKIP_LLAMA=1"

if defined SKIP_LLAMA (
    echo [INFO] llama-server download skipped.
) else (
    "%PYTHON_BIN%" scripts\download_llama_server.py --if-missing
    if !ERRORLEVEL! NEQ 0 (
        echo [WARNING] llama-server auto-download skipped or failed, continuing...
    )
)

:: 8. Verify llama-server engine presence
if not exist "bin\llama-server.exe" (
    echo.
    echo [WARNING] Движок llama-server не установлен в папку bin\. Скачайте его через меню "«Параметры и настройки -> Движок LLM»".
)

echo.
echo =======================================================
echo [SUCCESS] NeonRoute installation completed successfully!
echo.
echo Launch options:
echo   - NeonRoute.lnk : desktop or project folder shortcut
echo   - run.bat       : standard silent launch without console
echo   - run_debug.bat : launch with visible debug console
echo =======================================================
if not "%1"=="--unattended" if not defined CI pause
exit /b 0
