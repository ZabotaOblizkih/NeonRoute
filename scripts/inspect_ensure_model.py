from pathlib import Path

lines = Path("services/lm_studio.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if "def ensure_model_loaded" in line:
        print(f"=== services/lm_studio.py (строки {idx+1} - {idx+55}) ===")
        for j in range(idx, min(len(lines), idx + 55)):
            print(f"{j+1:4d}: {lines[j]}")
        break
