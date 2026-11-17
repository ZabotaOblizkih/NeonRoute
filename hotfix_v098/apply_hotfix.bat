@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title NeonRoute v0.9.8-beta Hotfix

cd /d "%~dp0"
if not exist "services\" (
    if exist "..\services\" (
        cd ..
    )
)

echo =======================================================
echo    NeonRoute v0.9.8-beta Autonomous Hotfix Installer
echo =======================================================
echo.

set "PY_CMD="

if exist "runtime\python\python.exe" (
    set "PY_CMD=runtime\python\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PY_CMD=python"
    )
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python не найден! Убедитесь, что NeonRoute установлен.
    pause
    exit /b 1
)

echo [INFO] Интерпретатор: %PY_CMD%
echo.

"%PY_CMD%" "%~dp0hotfix_llama_v098.py"

echo.
echo =======================================================
pause
exit /b %ERRORLEVEL%
