from pathlib import Path
lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/subagent_engineer.py (строки 730-815) ===")
for j in range(729, min(len(lines), 815)):
    print(f"{j+1:4d} | {lines[j]}")
