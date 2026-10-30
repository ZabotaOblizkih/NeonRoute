from pathlib import Path
import re

# 1. Где объявлен _on_task_finished
print("=== 1. ПОИСК МЕТОДА _on_task_finished ===")
for p in Path(".").rglob("*.py"):
    if any(ign in p.parts for ign in [".git", "__pycache__", "venv", ".venv", "scripts"]):
        continue
    try:
        txt = p.read_text(encoding="utf-8")
        if "def _on_task_finished(" in txt:
            print(f"Найден в {p}:")
            lines = txt.splitlines()
            for i, l in enumerate(lines):
                if "def _on_task_finished(" in l:
                    for j in range(i, min(len(lines), i + 35)):
                        print(f"  {j+1:4d}: {lines[j]}")
                    break
    except Exception:
        pass

# 2. Логика replace_last в _sync_turn
print("\n=== 2. ЛОГИКА replace_last В _sync_turn (chat_flow_controller.py) ===")
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines_cf = p_cf.read_text(encoding="utf-8").splitlines()
    for i in range(250, min(len(lines_cf), 320)):
        print(f"{i+1:4d}: {lines_cf[i]}")

# 3. Воркер и приоритеты в task_queue.py
print("\n=== 3. ОБРАБОТКА ЗАДАЧ В services/task_queue.py ===")
p_tq = Path("services/task_queue.py")
if p_tq.exists():
    lines_tq = p_tq.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines_tq):
        if any(k in l for k in ("def _worker_loop", "def enqueue", "PRIORITY_LOW", "def _run_task")):
            print(f"Строка {i+1}: {l.strip()}")
            for j in range(i+1, min(len(lines_tq), i+20)):
                print(f"  {j+1:4d}: {lines_tq[j]}")
            print("  ---")
