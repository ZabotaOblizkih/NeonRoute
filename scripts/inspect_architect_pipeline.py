from pathlib import Path
import re

def inspect_file(filepath: Path, patterns: list):
    if not filepath.is_file():
        print(f"[-] Файл {filepath} не найден!")
        return
    print(f"\n{'='*20} {filepath.as_posix()} {'='*20}")
    lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, line in enumerate(lines, 1):
        if any(p.lower() in line.lower() for p in patterns):
            start = max(1, i - 2)
            end = min(len(lines), i + 3)
            print(f"--- Строка {i} ---")
            for j in range(start, end + 1):
                marker = ">>>" if j == i else "   "
                print(f"{j:4d} {marker} | {lines[j-1]}")

inspect_file(Path("services/engineer_planner.py"), [
    "class ArchitectPlan",
    "intent",
    "SYSTEM_PROMPT",
    "plan_architecture",
    "json"
])

inspect_file(Path("services/subagent_engineer.py"), [
    "plan.intent",
    "is_maintenance",
    "run_autonomous_cycle",
    "Autonomous loop"
])
