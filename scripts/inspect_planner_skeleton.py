from pathlib import Path
lines = Path("services/engineer_planner.py").read_text(encoding="utf-8", errors="ignore").splitlines()

targets = [idx for idx, line in enumerate(lines, 1) if any(k in line for k in ("target_lines_count > 250", "ASTSkeletonizer", "АРХИТЕКТУРНЫЙ СКЕЛЕТ"))]
print("Найдены строки в engineer_planner.py:", targets)

for t in targets:
    s = max(0, t - 15)
    e = min(len(lines), t + 35)
    print(f"\n=== engineer_planner.py (строки {s+1}-{e}) ===")
    for j in range(s, e):
        print(f"{j+1:4d} | {lines[j]}")
