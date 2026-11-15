@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul
title NeonRoute Installation and Setup
cd /d "%~dp0"

echo =======================================================
echo          NeonRoute - Installation and Setup
echo =======================================================
echo.

:: 1. Search for Python 3.10 or higher
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
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PYTHON_EXE% --version 2^>^&1') do (
    echo [OK] Found Python interpreter: %%v
)

:: 2. Create virtual environment
if exist "venv\Scripts\python.exe" goto :venv_exists

echo.
echo [1/3] Creating virtual environment in .\venv ...
%PYTHON_EXE% -m venv venv
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b %ERRORLEVEL%
)
echo [OK] Virtual environment created successfully.
goto :upgrade_pip

:venv_exists
echo.
echo [1/3] Virtual environment .\venv already exists. Updating packages...

:upgrade_pip
:: 3. Upgrade pip
echo.
echo [2/3] Checking and upgrading pip...
if exist "wheels\" (
    "venv\Scripts\python.exe" -m pip install --no-index --find-links=wheels --upgrade pip 2>nul
) else (
    "venv\Scripts\python.exe" -m pip install --upgrade pip
)
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Pip upgrade skipped or warning encountered, continuing...
)

:: 4. Install dependencies
echo.
echo [3/3] Installing dependencies from requirements.txt...
if exist "wheels\" (
    echo [OFFLINE] Found wheels directory. Installing from local cache...
    "venv\Scripts\python.exe" -m pip install --no-index --find-links=wheels -r requirements.txt
) else (
    echo [ONLINE] Installing dependencies from PyPI...
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Package installation failed.
    echo Please check network connection or wheels integrity and review the log above.
    echo =======================================================
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo =======================================================
echo [SUCCESS] NeonRoute installation completed successfully!
echo.
echo Launch options:
echo   - run.bat       : standard silent launch without console
echo   - run_debug.bat : launch with visible debug console
echo =======================================================
pause
exit /b 0
