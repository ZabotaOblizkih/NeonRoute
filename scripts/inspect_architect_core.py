from pathlib import Path
import re

print("=== ШАГ 1: Полный код get_architect_prompt в services/engineer_prompts.py ===")
ep_path = Path("services/engineer_prompts.py")
if ep_path.is_file():
    lines = ep_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    capturing = False
    for idx, line in enumerate(lines):
        if "def get_architect_prompt" in line:
            capturing = True
        if capturing:
            print(f"{idx+1:4d} | {line}")
            if idx > 0 and line.startswith("def ") and "get_architect_prompt" not in line:
                break
else:
    print("[-] services/engineer_prompts.py не найден!")

print("\n=== ШАГ 2: Все точки вызова get_architect_prompt в проекте ===")
for p in Path("services").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "get_architect_prompt" in txt:
        print(f"--> Файл: {p.as_posix()}")
        for i, l in enumerate(txt.splitlines()):
            if "get_architect_prompt" in l:
                print(f"    Строка {i+1:4d} | {l.strip()}")

print("\n=== ШАГ 2.1: Точка вызова и парсинга Архитектора в services/engineer_planner.py ===")
planner_path = Path("services/engineer_planner.py")
if planner_path.is_file():
    plines = planner_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, l in enumerate(plines):
        if any(k in l for k in ["get_architect_prompt", "arch_resp", "json.loads", "chat_completion"]):
            start = max(0, i - 3)
            end = min(len(plines), i + 8)
            print(f"--- Блок около строки {i+1} ---")
            for j in range(start, end):
                m = ">>>" if j == i else "   "
                print(f"{j+1:4d} {m} | {plines[j]}")
