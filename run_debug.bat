@echo off
title NeonRoute Debug Console
cd /d "%~dp0"

echo =======================================================
echo          NeonRoute - Debug Console
echo =======================================================
echo.

:: Priority 1: Autonomous embedded Python runtime
if exist "runtime\python\python.exe" (
    echo [INFO] Running with autonomous embedded Python runtime...
    "runtime\python\python.exe" main.py
    goto :after_run
)

:: Priority 2: Local virtual environment
if exist "venv\Scripts\python.exe" (
    echo [INFO] Running in local venv...
    "venv\Scripts\python.exe" main.py
    goto :after_run
)

:: Priority 3: Fallback check for system Python
python.exe -c "import sys; sys.exit(0)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] Running with system Python...
    python.exe main.py
    goto :after_run
)

echo [ERROR] NeonRoute environment is not configured.
echo Please run install.bat first to set up the environment.
echo =======================================================
pause
exit /b 1

:after_run
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo =======================================================
    echo Program exited with an error. Check the logs above.
    echo =======================================================
    pause
)
