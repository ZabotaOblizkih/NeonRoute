from pathlib import Path

# 1. Смотрим сигнатуру plan_architecture и ветку is_analysis
lines_planner = Path("services/engineer_planner.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== engineer_planner.py (строки 44-70) ===")
for i in range(44, min(70, len(lines_planner))):
    print(f"{i+1:3d} | {lines_planner[i]}")

# 2. Ищем, где живет get_architect_prompt
for p in Path("services").glob("**/*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "def get_architect_prompt" in txt:
        print(f"\n=== Найдено определение get_architect_prompt в {p} ===")
        plines = txt.splitlines()
        for j, l in enumerate(plines):
            if "def get_architect_prompt" in l:
                for k in range(j, min(j + 35, len(plines))):
                    print(f"{k+1:3d} | {plines[k]}")
                break
