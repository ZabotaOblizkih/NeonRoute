from pathlib import Path

lines = Path("services/engineer_parser.py").read_text(encoding="utf-8", errors="replace").splitlines()
for i in range(33, min(len(lines), 305) + 1):
    print(f"L{i:04d}: {lines[i-1]}")
