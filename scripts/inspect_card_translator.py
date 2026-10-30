from pathlib import Path

p = Path("services/card_translator.py")
if p.exists():
    lines = p.read_text(encoding="utf-8").splitlines()
    print(f"Всего строк в card_translator.py: {len(lines)}")
    for i in range(min(120, len(lines))):
        print(f"{i+1:4d}: {lines[i]}")
else:
    print("[!] services/card_translator.py не найден")
