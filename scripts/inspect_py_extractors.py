from pathlib import Path
import re

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()
print("--- Поиск _extract_pure_py и _has_python_code ---")

for idx, line in enumerate(lines):
    if "def _extract_pure_py" in line or "def _has_python_code" in line:
        print(f"Строка {idx+1:4d}: {line.strip()}")
        for sub_idx in range(idx, min(len(lines), idx + 25)):
            if lines[sub_idx].strip().startswith("def ") and sub_idx > idx:
                break
            print(f"    {lines[sub_idx].strip()[:75]}")
