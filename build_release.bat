@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul
title NeonRoute Release Builder Pipeline
cd /d "%~dp0"

echo =======================================================
echo          NeonRoute - Release Builder Pipeline
echo =======================================================
echo.

set "PY_EXE="
if exist "venv\Scripts\python.exe" (
    set "PY_EXE=venv\Scripts\python.exe"
    goto :run_builder
)

for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python") do (
    %%~C -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 and sys.version_info[1] >= 10 else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PY_EXE=%%~C"
        goto :run_builder
    )
)

:run_builder
if not defined PY_EXE (
    echo [ERROR] Python 3.10+ interpreter not found.
    pause
    exit /b 1
)

echo [INFO] Using Python: %PY_EXE%
%PY_EXE% scripts\build_release.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo =======================================================
    echo [ERROR] Release build failed!
    echo =======================================================
    pause
    exit /b %ERRORLEVEL%
)

echo.
pause
exit /b 0
