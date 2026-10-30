import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  СНЯТИЕ СЛЕПКОВ: load_session И ВХОД В МЕНЮ СИСТЕМА")
print("=" * 75)

# 1. Как устроен load_session в session_manager.py
print("\n[1] Фрагмент load_session в services/session_manager.py:")
sm_path = Path("services/session_manager.py")
if sm_path.exists():
    lines = sm_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        if "def load_session" in l:
            # смотрим тело метода до возврата словаря
            print("\n".join(f"  {lines[i]}" for i in range(idx, min(idx + 45, len(lines)))))
            break

# 2. Ищем, где подключается окно моделей генерации (ModelsManagerDialog)
print("\n[2] Где вызывается ModelsManagerDialog в проекте:")
for p in Path("ui").rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "modelsmanagerdialog" in txt.lower():
            for idx, l in enumerate(txt.splitlines()):
                if "modelsmanagerdialog" in l.lower():
                    print(f"  • {p.name}:{idx+1} -> {l.strip()}")
    except Exception:
        pass

# 3. Ищем кнопку или вызов системного меню в header_controller или menu_builder
print("\n[3] Поиск системного меню (System menu):")
for p in Path("ui/controllers").glob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "models_manager" in txt or "open_models_manager" in txt or "show_system_menu" in txt:
            print(f"  • Контроллер с вызовом диалогов: {p.name}")
    except Exception:
        pass

print("=" * 75)
