import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  СНЯТИЕ ТОЧНЫХ СЛЕПКОВ: load_session И ТЕСТ-СТЕНД РОУТИНГА")
print("=" * 75)

# 1. Чем заканчивается load_session в session_manager.py
print("\n[1] Окончание метода load_session в services/session_manager.py:")
sm_path = Path("services/session_manager.py")
if sm_path.exists():
    lines = sm_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    in_load = False
    return_lines = []
    for idx, l in enumerate(lines):
        if "def load_session" in l:
            in_load = True
        if in_load:
            if "return " in l:
                return_lines.append((idx + 1, l.strip()))
            if l.startswith("    def ") and "def load_session" not in l:
                break
    print("  Точки возврата из load_session:")
    for l_num, text in return_lines:
        print(f"    Строка {l_num}: {text}")

# 2. Как свёрстан тестовый стенд роутинга в ui/models_manager_dialog.py
print("\n[2] Фрагмент тестового стенда в ui/models_manager_dialog.py:")
mm_path = Path("ui/models_manager_dialog.py")
if mm_path.exists():
    code = mm_path.read_text(encoding="utf-8", errors="ignore")
    lines = code.splitlines()
    
    # Ищем RoutingResultBadge
    for idx, l in enumerate(lines):
        if "class RoutingResultBadge" in l:
            print("  --- Класс RoutingResultBadge ---")
            print("\n".join(f"    {lines[i]}" for i in range(idx, min(idx + 22, len(lines)))))
            break

    # Ищем запуск теста роутинга
    for idx, l in enumerate(lines):
        if "_run_routing_test" in l or "RoutingTestWorker" in l:
            print(f"\n  --- Найдено на строке {idx+1}: {l.strip()} ---")
            print("\n".join(f"    {lines[i]}" for i in range(idx, min(idx + 18, len(lines)))))
            break

print("=" * 75)
