@echo off
cd /d "%~dp0"

if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" main.py
    exit /b 0
)

pythonw.exe -c "import sys; sys.exit(0)" 2>nul
if %ERRORLEVEL% EQU 0 (
    start "" pythonw.exe main.py
    exit /b 0
)

if exist "venv\Scripts\python.exe" (
    start "" "venv\Scripts\python.exe" main.py
    exit /b 0
)

start "" python.exe main.py
exit /b 0
