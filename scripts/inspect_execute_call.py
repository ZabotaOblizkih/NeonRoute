import sys
from pathlib import Path

lines = Path("services/tool_engine.py").read_text(encoding="utf-8").splitlines()
for idx in range(3270, min(len(lines), 3330)):
    l = lines[idx]
    if l.strip():
        print(f"{idx+1:4d}: {l}")
