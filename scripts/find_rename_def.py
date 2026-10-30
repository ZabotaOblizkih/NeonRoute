from pathlib import Path
import re

for p in Path("ui").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "_on_rename_session_requested" in txt:
        for idx, l in enumerate(txt.splitlines()):
            if "def _on_rename_session_requested" in l:
                print(f"=== Найдено в {p.name}:{idx+1} ===")
                for j in range(idx, min(len(txt.splitlines()), idx + 25)):
                    print(f"{j+1:4d} | {txt.splitlines()[j]}")
                break
