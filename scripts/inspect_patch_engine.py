from pathlib import Path
import re

print("=== ПОИСК РЕАЛИЗАЦИИ patch_file В services/ ===")
for p in Path("services").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "patch_file" in txt:
        print(f"\n[Файл: {p.name}]")
        lines = txt.splitlines()
        for idx, line in enumerate(lines):
            if any(k in line for k in ["def patch_file", "def _apply_patch", "TOOL:patch_file"]):
                for j in range(max(0, idx - 2), min(len(lines), idx + 35)):
                    print(f"{j+1:4d} | {lines[j]}")
                print("-" * 50)
