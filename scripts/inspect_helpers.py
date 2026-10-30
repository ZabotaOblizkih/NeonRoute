from pathlib import Path

lines = Path("services/card_translator.py").read_text(encoding="utf-8").splitlines()
print("=== services/card_translator.py (строки 50 - 135) ===")
for j in range(49, min(len(lines), 135)):
    print(f"{j+1:4d}: {lines[j]}")
