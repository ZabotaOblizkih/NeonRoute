from pathlib import Path

mw_lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()

print("=== 1. ui/main_window.py: конец метода load_session (строки 1125-1170) ===")
for i in range(1124, min(1170, len(mw_lines))):
    print(f"{i+1:4d} | {mw_lines[i]}")

print("\n=== 2. ui/main_window.py: is_engineer_mode_active ===")
for idx, l in enumerate(mw_lines):
    if "def is_engineer_mode_active" in l:
        for j in range(idx, min(idx + 15, len(mw_lines))):
            print(f"{j+1:4d} | {mw_lines[j]}")
        break
