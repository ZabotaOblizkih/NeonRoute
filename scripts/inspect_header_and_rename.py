from pathlib import Path

print("=== 1. ui/components/workspace_inspector.py (поиск создания заголовка) ===")
insp_lines = Path("ui/components/workspace_inspector.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(insp_lines):
    if any(k in l.lower() for k in ["песочниц", "workspace", "header", "subsubtitle"]):
        for j in range(max(0, idx - 2), min(len(insp_lines), idx + 8)):
            print(f"{j+1:4d} | {insp_lines[j]}")
        print("-" * 30)

print("\n=== 2. ui/main_window.py (def _on_rename_session_requested) ===")
mw_lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(mw_lines):
    if "def _on_rename_session_requested" in l:
        for j in range(idx, min(len(mw_lines), idx + 25)):
            print(f"{j+1:4d} | {mw_lines[j]}")
        break
