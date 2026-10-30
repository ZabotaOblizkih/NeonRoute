from pathlib import Path

lines = Path("services/card_translator.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if "def build_field_prompt" in line or "FIELD_PROMPTS" in line:
        print(f"=== services/card_translator.py (строка {idx+1}) ===")
        for j in range(idx, min(len(lines), idx + 65)):
            print(f"{j+1:4d}: {lines[j]}")
            if lines[j].strip().startswith("def ") and j > idx + 2:
                break
        break
