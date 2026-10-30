import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  СНЯТИЕ СЛЕПКА: МЕХАНИКА _append_message И СТРИМИНГА ИНЖЕНЕРА")
print("=" * 75)

# 1. Как устроен метод _append_message в chat_flow_controller.py
print("\n[1] Метод _append_message в ui/controllers/chat_flow_controller.py:")
cf_path = Path("ui/controllers/chat_flow_controller.py")
if cf_path.exists():
    lines = cf_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        if "def _append_message(" in l or "def append_message(" in l:
            print("\n".join(f"  {lines[i]}" for i in range(idx, min(idx + 35, len(lines)))))
            break

# 2. Фрагмент создания и стриминга _turn_bubble в engineer_controller.py
print("\n[2] Фрагмент engineer_controller.py (строки 480-530, стриминг и создание бабла):")
eng_path = Path("ui/controllers/engineer_controller.py")
if eng_path.exists():
    lines = eng_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = max(0, 480)
    end = min(len(lines), 530)
    for i in range(start, end):
        print(f"  {i+1}: {lines[i]}")

print("=" * 75)
