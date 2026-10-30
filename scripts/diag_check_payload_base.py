from pathlib import Path

lines = Path("services/lm_studio.py").read_text(encoding="utf-8").splitlines()
for i in range(489, 545):
    print(f"{i+1:4d}: {lines[i]}")
