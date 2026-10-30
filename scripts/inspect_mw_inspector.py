from pathlib import Path
import re

mw_path = Path("ui/main_window.py")
lines = mw_path.read_text(encoding="utf-8", errors="ignore").splitlines()

print("=== 1. ui/main_window.py: логика загрузки сессии (строки 1075-1125) ===")
for i in range(1074, min(1125, len(lines))):
    print(f"{i+1:4d} | {lines[i]}")

print("\n=== 2. Все места управления workspace_inspector в main_window.py ===")
for idx, l in enumerate(lines):
    if "workspace_inspector" in l:
        print(f"Строка {idx+1:4d} | {l.strip()}")
