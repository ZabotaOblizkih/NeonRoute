from pathlib import Path

lines = Path("services/card_translator.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if "def translate_text_slot" in line:
        print(f"=== services/card_translator.py (строки {idx+1} - {idx+65}) ===")
        for j in range(idx, min(len(lines), idx + 65)):
            print(f"{j+1:4d}: {lines[j]}")
        break
