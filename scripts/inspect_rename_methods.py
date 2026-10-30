from pathlib import Path
import re

mw_lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== Поиск методов переименования в main_window.py ===")
for idx, l in enumerate(mw_lines):
    if any(k in l.lower() for k in ["rename", "переименов"]):
        for j in range(max(0, idx - 1), min(len(mw_lines), idx + 18)):
            print(f"{j+1:4d} | {mw_lines[j]}")
        print("-" * 30)
