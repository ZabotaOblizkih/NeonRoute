"""
tests/unit/test_dependency_dialog.py
===================================
Модульные тесты для диалогового окна «Проверка компонентов» (DependencyDialog):
- Инициализация FramelessDialog и FluentTitleBar
- Корректность отображения кнопки закрытия [X] в правом верхнем углу
- Проверка элементов интерфейса в сценарии, когда все зависимости установлены
- Проверка элементов интерфейса при наличии недостающих пакетов
- Динамическая адаптация цветовой палитры под цвет активных обоев (Material You / Fluent Tint)
- Сохранение настройки «Больше не показывать» в config.json
- Закрытие по нажатию клавиши Escape
"""

import os
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QPixmap, QColor, QKeyEvent
from PyQt6.QtWidgets import QApplication, QMainWindow
from qframelesswindow import FramelessDialog


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class DummyDepParent(QMainWindow):
    """Легковесный родительский виджет с поддержкой фона для тестов."""
    def __init__(self):
        super().__init__()
        self.background_pixmap = None


@pytest.fixture
def dummy_window(qapp):
    win = DummyDepParent()
    yield win
    win.deleteLater()


def test_dependency_dialog_all_ok(qapp, dummy_window, monkeypatch):
    """Проверяет создание диалога, когда все компоненты установлены."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert dialog.windowTitle() == "Проверка компонентов — NeonRoute"

    # Шапка FluentTitleBar: closeBtn видим, min/max скрыты
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # Элементы интерфейса
    assert dialog.btn_skip.text() == "Закрыть"
    assert dialog.btn_install is None
    assert hasattr(dialog, "cb_dont_show")

    dialog.deleteLater()


def test_dependency_dialog_missing_items(qapp, dummy_window, monkeypatch):
    """Проверяет отображение карточек недостающих пакетов и кнопки установки."""
    mock_missing = [
        {
            "name": "Pygame CE",
            "type": "python",
            "package": "pygame-ce",
            "description": "Игровой движок 2D",
            "consequence": "Режим Инженер не сможет запустить 2D игры",
        },
        {
            "name": "Git CLI",
            "type": "binary",
            "package": "git",
            "description": "Система контроля версий",
            "consequence": "Недоступен авто-коммит",
            "install_guide": "Скачайте с git-scm.com",
        }
    ]
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: mock_missing)

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()

    assert dialog.btn_install is not None
    assert dialog.btn_install.isEnabled() is True
    assert "pygame-ce" in dialog.checkboxes
    assert dialog.checkboxes["pygame-ce"].isChecked() is True

    dialog.deleteLater()


def test_dependency_dialog_adaptive_palette_from_wallpaper(qapp, dummy_window, monkeypatch):
    """Проверяет адаптивную перестройку палитры и кнопок под активные обои."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    from ui.dialogs.dependency_dialog import DependencyDialog
    from ui.common.palette_utils import extract_adaptive_palette

    default_pal = extract_adaptive_palette(None)
    dialog = DependencyDialog(dummy_window)
    dialog.show()

    assert dialog._current_palette["bg"] == default_pal["bg"]
    assert default_pal["btn"] in dialog.styleSheet()

    # Устанавливаем бирюзовые обои (#00F0FF)
    cyan_pixmap = QPixmap(64, 64)
    cyan_pixmap.fill(QColor("#00F0FF"))
    dummy_window.background_pixmap = cyan_pixmap

    dialog.update_adaptive_palette()

    assert dialog._current_palette["bg"] != default_pal["bg"]
    assert dialog._current_palette["btn"] in dialog.styleSheet()
    assert dialog._current_palette["accent"] in dialog.styleSheet()

    dialog.deleteLater()


def test_dependency_dialog_skip_saves_config(qapp, dummy_window, monkeypatch):
    """Проверяет сохранение флага check_dependencies_on_startup при включенном чекбоксе."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    mock_cfg = {"check_dependencies_on_startup": True}
    saved_cfg = {}

    def mock_load():
        return mock_cfg.copy()

    def mock_save(c):
        saved_cfg.update(c)

    monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", mock_load)
    monkeypatch.setattr("utils.config_manager.ConfigManager.save_config", mock_save)

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()

    dialog.cb_dont_show.setChecked(True)
    dialog.btn_skip.click()

    assert saved_cfg.get("check_dependencies_on_startup") is False
    assert dialog.isVisible() is False

    dialog.deleteLater()


def test_dependency_dialog_escape_key_closes(qapp, dummy_window, monkeypatch):
    """Проверяет закрытие диалога по нажатию клавиши Escape."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()
    assert dialog.isVisible() is True

    esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dialog.keyPressEvent(esc_event)

    assert dialog.isVisible() is False
    dialog.deleteLater()
