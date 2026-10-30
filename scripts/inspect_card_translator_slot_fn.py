from pathlib import Path

p = Path("services/card_translator.py")
lines = p.read_text(encoding="utf-8").splitlines()

for idx, line in enumerate(lines):
    if "def translate_card_slots_sequential" in line:
        print(f"=== translate_card_slots_sequential (строки {idx+1} - {min(len(lines), idx+35)}) ===")
        for i in range(idx, min(len(lines), idx + 35)):
            print(f"{i+1:4d}: {lines[i]}")
        break
