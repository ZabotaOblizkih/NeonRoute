@echo off
cd /d "%~dp0"

:: Проверяем локальный venv
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" main.py
    exit
)

:: Системный pythonw
start "" pythonw.exe main.py
exit
