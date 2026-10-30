from pathlib import Path

print("=== 1. ui/components/workspace_inspector.py (шапка виджета, первые 70 строк) ===")
insp_lines = Path("ui/components/workspace_inspector.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for i in range(min(70, len(insp_lines))):
    print(f"{i+1:3d} | {insp_lines[i]}")

print("\n=== 2. ui/main_window.py (логика _on_rename_session_requested) ===")
mw_lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(mw_lines):
    if "_on_rename_session_requested" in l:
        for j in range(idx, min(idx + 25, len(mw_lines))):
            print(f"{j+1:4d} | {mw_lines[j]}")
        break
