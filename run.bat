@echo off
cd /d "%~dp0"

:: Priority 1: Autonomous embedded Python runtime
if exist "runtime\python\pythonw.exe" (
    start "" "runtime\python\pythonw.exe" main.py
    exit /b 0
)

:: Priority 2: Local virtual environment
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" main.py
    exit /b 0
)

:: Priority 3: Fallback check for pythonw in system PATH
pythonw.exe -c "import sys; sys.exit(0)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    start "" pythonw.exe main.py
    exit /b 0
)

:: If not found, notify user to run install.bat
echo =======================================================
echo [ERROR] NeonRoute environment is not configured.
echo Please run install.bat first to set up the environment.
echo =======================================================
pause
exit /b 1
