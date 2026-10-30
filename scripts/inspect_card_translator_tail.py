from pathlib import Path

p = Path("services/card_translator.py")
lines = p.read_text(encoding="utf-8").splitlines()
print(f"Всего строк: {len(lines)}")
for i in range(119, len(lines)):
    print(f"{i+1:4d}: {lines[i]}")
