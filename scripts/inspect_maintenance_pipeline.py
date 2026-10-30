from pathlib import Path
import re

print("=== 1. ПОДГОТОВКА КОНТЕКСТА MAINTENANCE В services/subagent_engineer.py ===")
se_lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(se_lines):
    if any(k in l for k in ["ТЕКУЩАЯ ВЕРСИЯ ФАЙЛА", "is_maintenance", "plan.plan_content"]):
        start = max(0, idx - 2)
        end = min(len(se_lines), idx + 15)
        print(f"--- Строки {start+1}-{end} ---")
        for j in range(start, end):
            print(f"{j+1:4d} | {se_lines[j]}")
        break

print("\n=== 2. СИСТЕМНЫЙ ПРОМПТ ИНЖЕНЕРА В services/engineer_prompts.py ===")
ep_lines = Path("services/engineer_prompts.py").read_text(encoding="utf-8", errors="ignore").splitlines()
capturing_eng = False
for idx, l in enumerate(ep_lines):
    if "def get_engineer_system_prompt" in l:
        capturing_eng = True
    if capturing_eng:
        print(f"{idx+1:4d} | {l}")
        if idx > 0 and l.startswith("def ") and "get_engineer_system_prompt" not in l:
            break

print("\n=== 3. ФУНКЦИЯ RCA В services/engineer_prompts.py И ЕЁ ВЫЗОВЫ ===")
capturing_rca = False
for idx, l in enumerate(ep_lines):
    if "def get_rca_prompt" in l:
        capturing_rca = True
    if capturing_rca:
        print(f"{idx+1:4d} | {l}")
        if idx > 0 and l.startswith("def ") and "get_rca_prompt" not in l:
            break

print("\n--- Точки вызова get_rca_prompt в services/ ---")
for p in Path("services").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "get_rca_prompt" in txt:
        for i, line in enumerate(txt.splitlines()):
            if "get_rca_prompt" in line:
                print(f"{p.name}:{i+1} | {line.strip()}")
