from pathlib import Path
import re

for p in Path("services").glob("*.py"):
    txt = p.read_text(encoding="utf-8")
    if "_extract_pure_py" in txt:
        print(f"[*] Найдено _extract_pure_py в {p}")
        for idx, line in enumerate(txt.splitlines()):
            if "def _extract_pure_py" in line or "_extract_pure_py(" in line:
                print(f"    Строка {idx+1:4d}: {line.strip()[:80]}")
    if "_has_python_code" in txt:
        print(f"[*] Найдено _has_python_code в {p}")
        for idx, line in enumerate(txt.splitlines()):
            if "def _has_python_code" in line or "_has_python_code(" in line:
                print(f"    Строка {idx+1:4d}: {line.strip()[:80]}")
