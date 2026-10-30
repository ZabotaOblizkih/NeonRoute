from pathlib import Path

print("=== 1. services/subagent_engineer.py (вызов модели, строки 435-475) ===")
lines_se = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for i in range(434, min(475, len(lines_se))):
    print(f"{i+1:3d} | {lines_se[i]}")

print("\n=== 2. services/engineer_prompts.py (правила intent, строки 210-240) ===")
lines_ep = Path("services/engineer_prompts.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for j in range(209, min(240, len(lines_ep))):
    print(f"{j+1:3d} | {lines_ep[j]}")
