import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication, QWidget
from ui.widgets.engineer_task_card import CompactWorkspaceFileBadge

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
