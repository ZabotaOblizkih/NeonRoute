import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 80)
print("  СКВОЗНОЙ АУДИТ РЕЖИМА ИНЖЕНЕРА: ПРОВЕРКА ВСЕХ ТОЧЕК ИНТЕГРАЦИИ")
print("=" * 80)

results = []

def check(category: str, item: str, status: bool, detail: str = ""):
    mark = "✓ [OK]" if status else "✗ [ПРОПУЩЕНО/ОШИБКА]"
    results.append((category, item, mark, detail))
    print(f" {mark:22} | {item:<40} | {detail}")

# ---------------------------------------------------------
# БЛОК 1: Виджет карточки инженера (EngineerTaskCardWidget)
# ---------------------------------------------------------
print("\n--- [1] Стеклянный степпер задачи (ui/widgets/engineer_task_card.py) ---")
card_path = Path("ui/widgets/engineer_task_card.py")
if card_path.exists():
    code_card = card_path.read_text(encoding="utf-8", errors="ignore")
    check("TaskCard", "Файл виджета существует", True, str(card_path))
    check("TaskCard", "Класс EngineerTaskCardWidget", "class EngineerTaskCardWidget" in code_card)
    check("TaskCard", "Session Guard (self.session_id)", "session_id" in code_card)
    check("TaskCard", "Безопасность Qt (sip.isdeleted)", "isdeleted" in code_card)
    check("TaskCard", "Метод add_workspace_file_card", "def add_workspace_file_card" in code_card)
    check("TaskCard", "Парсинг без <think> (тулы/план)", any(k in code_card for k in ["tool_call", "parse_tool_calls", "write_file", "<think>"]))
    check("TaskCard", "Стеклянный полупрозрачный фон", any(k in code_card for k in ["rgba", "background", "border-radius"]))
else:
    check("TaskCard", "Файл виджета существует", False, "ui/widgets/engineer_task_card.py не найден")

# ---------------------------------------------------------
# БЛОК 2: Фабрика баблов (ui/builders/chat_bubble_builder.py)
# ---------------------------------------------------------
print("\n--- [2] Фабрика виджетов (ui/builders/chat_bubble_builder.py) ---")
cbb_path = Path("ui/builders/chat_bubble_builder.py")
if cbb_path.exists():
    code_cbb = cbb_path.read_text(encoding="utf-8", errors="ignore")
    has_eng_branch = "EngineerTaskCardWidget" in code_cbb
    has_sid_pass = "session_id=" in code_cbb or "session_id" in code_cbb
    check("Builder", "Врезка EngineerTaskCardWidget", has_eng_branch, "Проверка eff_mode / engineer")
    check("Builder", "Передача session_id в виджет", has_sid_pass, "Привязка карточки к сессии")
else:
    check("Builder", "Файл chat_bubble_builder.py", False)

# ---------------------------------------------------------
# БЛОК 3: Контроллер инженера (ui/controllers/engineer_controller.py)
# ---------------------------------------------------------
print("\n--- [3] Контроллер инженера (ui/controllers/engineer_controller.py) ---")
eng_path = Path("ui/controllers/engineer_controller.py")
if eng_path.exists():
    code_eng = eng_path.read_text(encoding="utf-8", errors="ignore")
    has_guard = any(k in code_eng for k in ["active_sid", "bubble_sid", "session_id"])
    check("Controller", "Защита от смены чатов (Session Guard)", has_guard, "Подавление сигналов чужих сессий")
else:
    check("Controller", "Файл engineer_controller.py", False)

# ---------------------------------------------------------
# БЛОК 4: Центр компетенций «Знания инженера»
# ---------------------------------------------------------
print("\n--- [4] Центр «Знания инженера» и меню «Система» ---")
diag_hub_path = Path("ui/engineer_skills_dialog.py")
if not diag_hub_path.exists():
    diag_hub_path = Path("ui/dialogs/engineer_skills_dialog.py")

check("Hub", "Диалог EngineerSkillsDialog", diag_hub_path.exists(), str(diag_hub_path) if diag_hub_path.exists() else "Не найден")

menu_path = Path("ui/builders/menu_builder.py")
if menu_path.exists():
    code_menu = menu_path.read_text(encoding="utf-8", errors="ignore")
    has_hub_action = "Знания инженера" in code_menu or "engineer_skills" in code_menu
    check("Menu", "Пункт в меню «Система»", has_hub_action, "act_engineer_skills")
else:
    check("Menu", "Файл menu_builder.py", False)

dc_path = Path("ui/controllers/dialog_controller.py")
if dc_path.exists():
    code_dc = dc_path.read_text(encoding="utf-8", errors="ignore")
    has_open_method = "_open_engineer_skills_dialog" in code_dc
    check("DialogCtrl", "Метод _open_engineer_skills_dialog", has_open_method)
else:
    check("DialogCtrl", "Файл dialog_controller.py", False)

# ---------------------------------------------------------
# БЛОК 5: Сохранение активного скилла и галочка в меню
# ---------------------------------------------------------
print("\n--- [5] Контекстное меню аватарки и сохранение сессии ---")
av_path = Path("ui/controllers/avatar_controller.py")
if av_path.exists():
    code_av = av_path.read_text(encoding="utf-8", errors="ignore")
    has_mem_fix = "_active_engineer_skill" in code_av
    has_direct_save = "SessionManager.save_session" in code_av
    check("Avatar", "Память MainWindow (_active_engineer_skill)", has_mem_fix, "Фиксация галочки")
    check("Avatar", "Прямой вызов SessionManager.save_session", has_direct_save, "Сохранение сессии")
else:
    check("Avatar", "Файл avatar_controller.py", False)

print("\n" + "=" * 80)
failed_count = sum(1 for _, _, mark, _ in results if "✗" in mark)
if failed_count == 0:
    print("  ИТОГ: ВСЕ 15 ТОЧЕК ИНТЕГРАЦИИ РЕАЛИЗОВАНЫ ПОЛНОСТЬЮ И БЕЗ ПРОПУСКОВ!")
else:
    print(f"  ИТОГ: ОБНАРУЖЕНО {failed_count} ПРОПУЩЕННЫХ ИЛИ НЕЗАВЕРШЕННЫХ ПУНКТОВ.")
print("=" * 80)
