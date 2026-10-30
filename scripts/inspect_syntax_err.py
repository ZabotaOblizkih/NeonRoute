from pathlib import Path

lines = Path("services/card_translator.py").read_text(encoding="utf-8").splitlines()
print(f"Всего строк: {len(lines)}")
print("=== Строки 140 - 165 ===")
for i in range(max(0, 139), min(len(lines), 165)):
    print(f"{i+1:4d}: {lines[i]}")
