from pathlib import Path

lines = Path("services/lm_studio.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if "def is_model_loaded" in line or "def get_loaded_models" in line:
        print(f"=== services/lm_studio.py (строки {idx+1} - {idx+30}) ===")
        for j in range(idx, min(len(lines), idx + 30)):
            print(f"{j+1:4d}: {lines[j]}")
