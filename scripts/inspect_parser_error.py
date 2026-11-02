from pathlib import Path

lines = Path("services/engineer_parser.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 195–225 из services/engineer_parser.py ---")
for idx in range(194, min(len(lines), 225)):
    print(f"{idx+1:4d}: {lines[idx]}")
