from pathlib import Path

p = Path("services/task_queue.py")
if p.exists():
    lines = p.read_text(encoding="utf-8").splitlines()
    print(f"Всего строк в task_queue.py: {len(lines)}")
    for i in range(min(120, len(lines))):
        print(f"{i+1:4d}: {lines[i]}")
else:
    print("[!] services/task_queue.py не найден")
