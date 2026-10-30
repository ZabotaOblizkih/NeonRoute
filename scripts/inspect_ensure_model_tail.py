from pathlib import Path

lines = Path("services/lm_studio.py").read_text(encoding="utf-8").splitlines()
print("=== services/lm_studio.py (строки 341 - 435) ===")
for j in range(340, min(len(lines), 435)):
    print(f"{j+1:4d}: {lines[j]}")
