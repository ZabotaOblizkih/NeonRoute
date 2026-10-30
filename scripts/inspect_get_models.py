from pathlib import Path

lines = Path("services/lm_studio.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if "def get_models" in line or "def check_health" in line:
        print(f"=== {line.strip()} (строки {idx+1} - {idx+25}) ===")
        for j in range(idx, min(len(lines), idx + 25)):
            print(f"{j+1:4d}: {lines[j]}")
