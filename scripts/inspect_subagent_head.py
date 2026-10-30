from pathlib import Path
lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/subagent_engineer.py (строки 630-680) ===")
for j in range(629, min(len(lines), 680)):
    print(f"{j+1:4d} | {lines[j]}")
