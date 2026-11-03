import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 1. Создаем ui/common/ui_context.py
ui_context_file = PROJECT_ROOT / "ui" / "common" / "ui_context.py"
ui_context_code = """\"\"\"
ui_context.py — Единый источник правды (SSOT) состояния интерфейса и диалогового цикла NeonRoute.
Инкапсулирует сессию, активный бабл стриминга и флаги занятости, устраняя дефект 'хрупкого базового класса'.
\"\"\"
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("NeonRoute.UIContext")

class UIContext:
    \"\"\"Контейнер состояния интерфейса и диалогового цикла MainWindow.\"\"\"

    def __init__(self, main_window: Optional[Any] = None):
        self._main_window = main_window
        self._current_session: Optional[Dict[str, Any]] = None
        self._turn_bubble: Optional[Any] = None
        self._is_busy: bool = False
        self._is_generating: bool = False

    @property
    def current_session(self) -> Optional[Dict[str, Any]]:
        return self._current_session

    @current_session.setter
    def current_session(self, value: Optional[Dict[str, Any]]) -> None:
        old_id = self._current_session.get("id") if isinstance(self._current_session, dict) else None
        new_id = value.get("id") if isinstance(value, dict) else None
        if old_id != new_id:
            logger.debug(f"[UIContext] Смена сессии: {old_id} -> {new_id}")
        self._current_session = value

    @property
    def turn_bubble(self) -> Optional[Any]:
        return self._turn_bubble

    @turn_bubble.setter
    def turn_bubble(self, widget: Optional[Any]) -> None:
        self._turn_bubble = widget

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @is_busy.setter
    def is_busy(self, val: bool) -> None:
        self._is_busy = bool(val)

    def begin_turn(self, bubble_widget: Any) -> None:
        \"\"\"Атомарный старт хода генерации ответа.\"\"\"
        self._turn_bubble = bubble_widget
        self._is_generating = True
        self._is_busy = True

    def finish_turn(self) -> None:
        \"\"\"Атомарное завершение хода генерации ответа.\"\"\"
        self._turn_bubble = None
        self._is_generating = False
        self._is_busy = False

    def get_session_id(self) -> Optional[str]:
        \"\"\"Безопасное получение ID активной сессии.\"\"\"
        if isinstance(self._current_session, dict):
            return self._current_session.get("id")
        return None

    def get_mode(self) -> Optional[str]:
        \"\"\"Безопасное получение режима активной сессии.\"\"\"
        if isinstance(self._current_session, dict):
            return self._current_session.get("mode")
        return None
"""

ui_context_file.write_text(ui_context_code.strip() + "\n", encoding="utf-8")
print(f"[OK] Создан модуль: {ui_context_file.relative_to(PROJECT_ROOT)}")

# 2. Интегрируем UIContext в ui/main_window.py
mw_file = PROJECT_ROOT / "ui" / "main_window.py"
mw_lines = mw_file.read_text(encoding="utf-8").splitlines()

# Добавляем импорт UIContext
import_added = False
for idx, l in enumerate(mw_lines):
    if "from ui.common.icon_utils import" in l or "from ui.styles import" in l:
        mw_lines.insert(idx, "from ui.common.ui_context import UIContext")
        import_added = True
        break

if not import_added:
    mw_lines.insert(30, "from ui.common.ui_context import UIContext")

# В MainWindow.__init__ инициализируем self.ui_context
for idx, l in enumerate(mw_lines):
    if "def __init__(self" in l:
        # Ищем вызов super().__init__
        for sub_idx in range(idx, idx + 20):
            if "super().__init__" in mw_lines[sub_idx]:
                mw_lines.insert(sub_idx + 1, "        self.ui_context = UIContext(self)")
                break
        break

# Добавляем свойства-делегаты в класс MainWindow (после __init__)
properties_block = [
    "    # --- Единый источник правды (SSOT): делегирование в self.ui_context ---",
    "    @property",
    "    def current_session(self) -> Optional[Dict[str, Any]]:",
    "        return self.ui_context.current_session if hasattr(self, 'ui_context') else getattr(self, '_legacy_current_session', None)",
    "",
    "    @current_session.setter",
    "    def current_session(self, val: Optional[Dict[str, Any]]) -> None:",
    "        if hasattr(self, 'ui_context'):",
    "            self.ui_context.current_session = val",
    "        else:",
    "            self._legacy_current_session = val",
    "",
    "    @property",
    "    def _turn_bubble(self) -> Optional[Any]:",
    "        return self.ui_context.turn_bubble if hasattr(self, 'ui_context') else getattr(self, '_legacy_turn_bubble', None)",
    "",
    "    @_turn_bubble.setter",
    "    def _turn_bubble(self, val: Optional[Any]) -> None:",
    "        if hasattr(self, 'ui_context'):",
    "            self.ui_context.turn_bubble = val",
    "        else:",
    "            self._legacy_turn_bubble = val",
    ""
]

# Вставляем блок свойств перед первым методом MainWindow
for idx, l in enumerate(mw_lines):
    if l.startswith("    def init_ui(") or l.startswith("    def _setup_ui("):
        mw_lines = mw_lines[:idx] + properties_block + mw_lines[idx:]
        break

mw_file.write_text("\n".join(mw_lines) + "\n", encoding="utf-8")
print(f"[OK] ui/main_window.py обновлен: интегрирован UIContext со свойствами-делегатами.")

# 3. Запуск смок-тестов
print("\n>>> Запуск смок-теста MainWindow...")
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_mainwindow_lifecycle.py", "-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тест провален!")
    sys.exit(res.returncode)

print("[PASS] Смок-тест MainWindow успешно пройден с UIContext!")
