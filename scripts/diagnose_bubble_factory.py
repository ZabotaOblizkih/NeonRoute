import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  СНЯТИЕ СЛЕПКА: ФАБРИКА ВИДЖЕТОВ И ОЧИСТКА ЧАТА")
print("=" * 75)

# 1. Как _append_message создает виджет и добавляет его в layout
print("\n[1] Создание виджета внутри ui/builders/chat_bubble_builder.py (строки 424-485):")
cbb_path = Path("ui/builders/chat_bubble_builder.py")
if cbb_path.exists():
    lines = cbb_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = 423
    end = min(len(lines), start + 65)
    for i in range(start, end):
        print(f"  {i+1}: {lines[i]}")

# 2. Как clear_chat_view очищает виджеты
print("\n[2] Метод clear_chat_view в ui/controllers/chat_flow_controller.py (строки 235-265):")
cf_path = Path("ui/controllers/chat_flow_controller.py")
if cf_path.exists():
    lines = cf_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = 234
    end = min(len(lines), start + 30)
    for i in range(start, end):
        print(f"  {i+1}: {lines[i]}")

print("=" * 75)
