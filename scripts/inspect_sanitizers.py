from pathlib import Path

lines = Path("services/card_translator.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if "def sanitize" in line or "def clean" in line or "def unmask" in line:
        print(f"=== {line.strip()} (строка {idx+1}) ===")
        for j in range(idx, min(len(lines), idx + 25)):
            print(f"{j+1:4d}: {lines[j]}")
            if lines[j].strip().startswith("def ") and j > idx:
                break
