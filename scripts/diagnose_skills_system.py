import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  СНЯТИЕ СЛЕПКОВ: СЕССИИ, МЕНЮ СИСТЕМА И СТЕНД ТЕСТИРОВАНИЯ")
print("=" * 75)

# 1. Проверяем сохранение metadata в session_manager.py
print("\n[1] Проверка сохранения 'metadata' в services/session_manager.py:")
sm_path = Path("services/session_manager.py")
if sm_path.exists():
    lines = sm_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        if "def save_session" in l:
            print("\n".join(f"  {lines[i]}" for i in range(idx, min(idx + 35, len(lines)))))
            break

# 2. Проверяем, как строится меню 'Система' в ui/builders/menu_builder.py
print("\n[2] Поиск меню 'Система' в ui/builders/menu_builder.py:")
mb_path = Path("ui/builders/menu_builder.py")
if mb_path.exists():
    lines = mb_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        if "система" in l.lower() or "system" in l.lower():
            start = max(0, idx - 5)
            end = min(len(lines), idx + 25)
            print("\n".join(f"  {lines[i]}" for i in range(start, end)))
            break

# 3. Смотрим структуру диалога моделей генерации (ui/models_manager_dialog.py)
print("\n[3] Структура ui/models_manager_dialog.py:")
mm_path = Path("ui/models_manager_dialog.py")
if mm_path.exists():
    lines = mm_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    classes = [l for l in lines if l.startswith("class ")]
    print("  Классы в файле:", classes)
    # Поиск таблицы или стенда тестирования
    test_bench_lines = [l for l in lines if "test" in l.lower() or "table" in l.lower() or "score" in l.lower()]
    print(f"  Найдено строк с упоминанием тестов/таблиц: {len(test_bench_lines)}")
    for l in test_bench_lines[:8]:
        print(f"    {l.strip()}")

print("=" * 75)
