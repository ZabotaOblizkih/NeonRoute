from pathlib import Path
lines = Path("services/code_engine.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/code_engine.py (строки 115-210) ===")
for j in range(114, min(len(lines), 210)):
    print(f"{j+1:4d} | {lines[j]}")
