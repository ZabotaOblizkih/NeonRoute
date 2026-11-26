import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication, QWidget
from ui.widgets.engineer_task_card import CompactWorkspaceFileBadge, EngineerTaskCardWidget
from ui.components.thought_spoiler_widget import ThoughtSpoilerWidget

@pytest.fixture(scope="session")
def qapp():
    """Headless Qt Application fixture."""
    app = QApplication.instance()
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication(["-platform", "offscreen"])
    return app

def test_badge_inspector_nav_does_not_execute_script(qapp):
    """Регрессионный тест: клик 'В инспектор' открывает файл без побочного запуска процесса."""
    mock_inspector = MagicMock()
    mock_window = QWidget()
    mock_window.workspace_inspector = mock_inspector

    run_mock = MagicMock()
    test_file = Path("C:/NeonRoute/workspace/game.py")

    badge = CompactWorkspaceFileBadge(
        file_path=test_file,
        on_run=run_mock,
        action_text="workspace / game.py",
        parent=mock_window
    )
    badge.window = MagicMock(return_value=mock_window)

    # Имитируем клик по кнопке «📄 В инспектор»
    badge._on_inspect_clicked()

    # 1. Проверяем, что инспектор отобразился и принял файл
    assert mock_inspector.setVisible.called, "Инспектор должен стать видимым (setVisible(True))"
    assert mock_inspector.open_file.called, "Метод open_file должен быть вызван"
    assert mock_inspector.open_file.call_args[0][0] == test_file, "open_file должен получить целевой файл"

    # 2. Ключевая проверка отсутствия сайд-эффекта запуска
    assert not run_mock.called, "on_run НЕ должен вызываться при навигации в инспектор!"


def test_engineer_task_card_dual_thought_spoilers(qapp):
    """
    Проверяет разделение хода мыслей в EngineerTaskCardWidget на два отдельных
    ThoughtSpoilerWidget для фаз 'architect' и 'worker' с разными заголовками.
    """
    widget = EngineerTaskCardWidget(sender="Элария", text="Запуск задачи", is_active=True)

    # 1. Стриминг мыслей Архитектора
    widget.append_thought_chunk("Проектирую структуру игры...", phase="architect")
    widget.finalize_thought(duration_sec=1.2, token_count=45, phase="architect")

    # 2. Стриминг мыслей Работяги-Инженера
    widget.append_thought_chunk("Пишу игровой цикл и обработку событий...", phase="worker")
    widget.finalize_thought(duration_sec=2.8, token_count=130, phase="worker")

    # Проверяем наличие двух независимых спойлеров
    assert len(widget._thought_spoilers) == 2
    assert "architect" in widget._thought_spoilers
    assert "worker" in widget._thought_spoilers

    arch_spoiler = widget._thought_spoilers["architect"]
    worker_spoiler = widget._thought_spoilers["worker"]

    assert isinstance(arch_spoiler, ThoughtSpoilerWidget)
    assert isinstance(worker_spoiler, ThoughtSpoilerWidget)

    # Проверяем заголовки
    assert arch_spoiler.title_lbl.text() == "Ход мыслей · Архитектор"
    assert worker_spoiler.title_lbl.text() == "Ход мыслей · Инженер"

    # Проверяем содержимое
    assert "Проектирую структуру" in arch_spoiler.get_thought_text()
    assert "Пишу игровой цикл" in worker_spoiler.get_thought_text()

    # Проверяем метрики завершения
    assert "45 токенов" in arch_spoiler.metrics_label.text()
    assert "130 токенов" in worker_spoiler.metrics_label.text()

    widget.deleteLater()

