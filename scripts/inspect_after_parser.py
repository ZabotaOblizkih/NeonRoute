import re
from pathlib import Path

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 835–950 из services/subagent_engineer.py ---")

for idx in range(834, min(len(lines), 950)):
    line = lines[idx]
    if re.match(r"^\s{12}(#\s*.*|if\s+.*:|for\s+.*:)", line):
        print(f"Строка {idx+1:4d}: {line.strip()}")
