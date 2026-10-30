from pathlib import Path

mw_lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(mw_lines):
    if "_on_rename_session_requested" in l and "def " in l:
        print(f"=== ui/main_window.py (строка {idx+1}) ===")
        for j in range(idx, min(len(mw_lines), idx + 35)):
            print(f"{j+1:4d} | {mw_lines[j]}")
        break
