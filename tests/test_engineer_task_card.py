import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6 import sip

# Ensure single QApplication instance
app = QApplication.instance() or QApplication([])

from ui.widgets.engineer_task_card import (
    EngineerTaskCardWidget,
    EngineerGlassCard,
    CompactWorkspaceFileBadge,
    EngineerSpoilerWidget
)


def test_card_instantiation_and_session_id():
    w = EngineerTaskCardWidget(
        sender="Элария",
        text="Тестовая задача",
        session_id="session_xyz_123"
    )
    assert w.session_id == "session_xyz_123"
    assert w.sender == "Элария"
    # Аватарка демонтирована из UI инженера (нет левой колонки с аватаром)
    assert getattr(w, "avatar_lbl", None) is None
    assert isinstance(w.card, EngineerGlassCard)
    # Наличие Activity Header
    assert hasattr(w, "phase_indicator")
    assert hasattr(w, "skill_badge")
    assert hasattr(w, "timer_lbl")


def test_scenario_1_with_think_tags():
    raw_text = "<think>Анализирую требования к архитектуре песочницы...</think>Архитектура спроектирована и готова к запуску."
    w = EngineerTaskCardWidget(
        sender="Элария",
        text=raw_text,
        session_id="sess_1"
    )
    w.show()
    assert not w.spoiler.isHidden()
    assert "Анализирую требования" in w.spoiler.get_plan_text()
    assert "Архитектура спроектирована" in w.summary_browser.toPlainText()
    assert "<think>" not in w.summary_browser.toPlainText()
    assert w.spoiler.is_collapsed


def test_scenario_2_no_think_with_tools():
    raw_text = """Декомпозиция задачи:
1. Создать модуль test_runner.py
2. Выполнить проверку
<<TOOL:write_file filename="test_runner.py">>
def run(): pass
<</TOOL>>
Все проверки успешно пройдены!"""

    w = EngineerTaskCardWidget(
        sender="Элария",
        text=raw_text,
        session_id="sess_2"
    )
    w.show()
    assert not w.spoiler.isHidden()
    assert "Декомпозиция задачи" in w.spoiler.get_plan_text()
    assert "Архитектура" in w.spoiler.title_lbl.text()
    assert "Все проверки успешно пройдены!" in w.summary_browser.toPlainText()
    assert "<<TOOL:" not in w.summary_browser.toPlainText()
    assert w.spoiler.is_collapsed


def test_scenario_3_pure_text_no_tools_no_think():
    raw_text = "Привет! Я готова помочь с реализацией алгоритма или настройкой окружения."
    w = EngineerTaskCardWidget(
        sender="Элария",
        text=raw_text,
        session_id="sess_3"
    )
    w.show()
    # Спойлер полностью скрывается
    assert w.spoiler.isHidden()
    assert "Привет! Я готова помочь" in w.summary_browser.toPlainText()


def test_compact_file_stepper():
    w = EngineerTaskCardWidget(
        sender="Элария",
        text="Создан скрипт",
        session_id="sess_stepper"
    )
    w.show()
    test_path = Path("workspace/calculator.py")
    badge = w.add_workspace_file_card(test_path)
    assert not w.files_container.isHidden()
    assert isinstance(badge, CompactWorkspaceFileBadge)
    assert badge.file_path.name == "calculator.py"
    assert "📄 В инспектор" in badge.btn_inspect.text()

    # Повторное добавление того же файла возвращает существующий бейдж (дедупликация)
    badge2 = w.add_workspace_file_card(test_path)
    assert badge is badge2


def test_step_addition():
    w = EngineerTaskCardWidget(
        sender="Элария",
        text="Шаги",
        session_id="sess_steps"
    )
    w.show()
    step1 = w.add_step("Чтение workspace/main.py", status="completed")
    step2 = w.add_step("Запуск pytest tests/test_engine.py", status="running")
    assert not w.files_container.isHidden()
    assert step1.glyph_lbl.text() == "✓"
    assert step2.glyph_lbl.text() == "●"


def test_sip_deleted_safety():
    w = EngineerTaskCardWidget(
        sender="Элария",
        text="Тест безопасности",
        session_id="sess_del"
    )
    # Вызовы публичных методов до удаления
    w.set_text("Новый текст")
    w.append_stream_chunk("добавка")
    w.finalize_thought(1.5, 42)
    w.toggle_spoiler()

    # Удаление виджета
    w.deleteLater()
    app.processEvents()

    # Проверка вызова методов на удаленном виджете не должна бросать RuntimeError
    if sip.isdeleted(w):
        # Метод безопасно прерывается по sip.isdeleted
        w.set_text("Попытка после удаления")
        w.add_workspace_file_card(Path("any.py"))
        w.copy_text()
        w.finalize_thought(2.0, 10)
