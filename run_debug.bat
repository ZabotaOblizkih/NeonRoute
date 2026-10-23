@echo off
title NeonRoute Debug Console
cd /d "%~dp0"

:: Проверяем локальный venv
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" main.py
) else (
    python.exe main.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo =======================================================
    echo Program exited with an error. Check the logs above.
    echo =======================================================
    pause
)
