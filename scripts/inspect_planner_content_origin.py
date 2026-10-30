from pathlib import Path
lines = Path("services/engineer_planner.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/engineer_planner.py (строки 190-270) ===")
for j in range(189, min(len(lines), 270)):
    print(f"{j+1:4d} | {lines[j]}")
