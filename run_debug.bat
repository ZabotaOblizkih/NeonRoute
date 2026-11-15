@echo off
title NeonRoute Debug Console
cd /d "%~dp0"

echo =======================================================
echo          NeonRoute - Debug Console
echo =======================================================
echo.

if exist "venv\Scripts\python.exe" (
    echo [INFO] Running in local venv...
    "venv\Scripts\python.exe" main.py
) else (
    echo [INFO] Running with system Python...
    python.exe main.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo =======================================================
    echo Program exited with an error. Check the logs above.
    echo =======================================================
    pause
)
