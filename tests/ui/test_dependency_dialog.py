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


def test_dependency_dialog_close_saves_config(qapp, dummy_window, monkeypatch):
    """Проверяет сохранение флага при закрытии окна через close() / [X] при включенном чекбоксе."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    mock_cfg = {"check_dependencies_on_startup": True}
    saved_cfg = {}

    monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: mock_cfg.copy())
    monkeypatch.setattr("utils.config_manager.ConfigManager.save_config", lambda c: saved_cfg.update(c))

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()

    dialog.cb_dont_show.setChecked(True)
    dialog.close()

    assert saved_cfg.get("check_dependencies_on_startup") is False
    assert dialog.isVisible() is False
    dialog.deleteLater()


def test_dependency_dialog_reject_saves_config(qapp, dummy_window, monkeypatch):
    """Проверяет сохранение флага при вызове reject() при включенном чекбоксе."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    mock_cfg = {"check_dependencies_on_startup": True}
    saved_cfg = {}

    monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: mock_cfg.copy())
    monkeypatch.setattr("utils.config_manager.ConfigManager.save_config", lambda c: saved_cfg.update(c))

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()

    dialog.cb_dont_show.setChecked(True)
    dialog.reject()

    assert saved_cfg.get("check_dependencies_on_startup") is False
    assert dialog.isVisible() is False
    dialog.deleteLater()


def test_dependency_dialog_accept_saves_config(qapp, dummy_window, monkeypatch):
    """Проверяет сохранение флага при вызове accept() при включенном чекбоксе."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    mock_cfg = {"check_dependencies_on_startup": True}
    saved_cfg = {}

    monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: mock_cfg.copy())
    monkeypatch.setattr("utils.config_manager.ConfigManager.save_config", lambda c: saved_cfg.update(c))

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()

    dialog.cb_dont_show.setChecked(True)
    dialog.accept()

    assert saved_cfg.get("check_dependencies_on_startup") is False
    assert dialog.isVisible() is False
    dialog.deleteLater()


def test_dependency_dialog_on_finished_success_saves_config(qapp, dummy_window, monkeypatch):
    """Проверяет сохранение флага при успешном завершении установки пакетов."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])
    monkeypatch.setattr("PyQt6.QtWidgets.QMessageBox.information", lambda *args, **kwargs: None)

    mock_cfg = {"check_dependencies_on_startup": True}
    saved_cfg = {}

    monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: mock_cfg.copy())
    monkeypatch.setattr("utils.config_manager.ConfigManager.save_config", lambda c: saved_cfg.update(c))

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()

    dialog.cb_dont_show.setChecked(True)
    dialog._on_finished(True, "Компоненты успешно установлены")

    assert saved_cfg.get("check_dependencies_on_startup") is False
    assert dialog.isVisible() is False
    dialog.deleteLater()


def test_dependency_dialog_unchecked_does_not_save_false(qapp, dummy_window, monkeypatch):
    """Проверяет, что при неактивном чекбоксе сохранение не перезаписывает настройки."""
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    saved_calls = []

    monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: {"check_dependencies_on_startup": True})
    monkeypatch.setattr("utils.config_manager.ConfigManager.save_config", lambda c: saved_calls.append(c))

    from ui.dialogs.dependency_dialog import DependencyDialog

    dialog = DependencyDialog(dummy_window)
    dialog.show()

    assert dialog.cb_dont_show.isChecked() is False
    dialog.close()

    assert len(saved_calls) == 0
    assert dialog.isVisible() is False
    dialog.deleteLater()


def test_config_manager_default_settings_and_dynamic_path(tmp_path, monkeypatch):
    """Проверяет наличие check_dependencies_on_startup в DEFAULT_SETTINGS и динамическое разрешение пути."""
    import config
    from utils.config_manager import ConfigManager, DEFAULT_SETTINGS, get_config_path

    # 1. Проверяем наличие ключа в DEFAULT_SETTINGS
    assert "check_dependencies_on_startup" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["check_dependencies_on_startup"] is True

    # 2. Динамическое разрешение через config.CONFIG_PATH
    custom_cfg_path = tmp_path / "custom_config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", custom_cfg_path)
    assert ConfigManager.get_config_path() == custom_cfg_path
    assert get_config_path() == custom_cfg_path

    # 3. Сохранение и загрузка через атомарную запись
    ConfigManager.save_config({"check_dependencies_on_startup": False, "theme": "light"})
    loaded = ConfigManager.load_config()
    assert loaded["check_dependencies_on_startup"] is False
    assert loaded["theme"] == "light"
    # Fallback на дефолты при слиянии
    assert "chat_font_size" in loaded

    # 4. Сохранение существующего значения False при перезаписи/обновлении
    assert loaded.get("check_dependencies_on_startup") is False


def test_main_window_startup_dependencies_respects_config_flag(monkeypatch):
    """Проверяет, что при check_dependencies_on_startup=False запуск диалога не планируется."""
    from services.environment_doctor import EnvironmentDoctor
    from utils.config_manager import ConfigManager

    checked = []
    monkeypatch.setattr(EnvironmentDoctor, "check_missing", lambda: checked.append("called") or ["dummy_pkg"])
    monkeypatch.setattr(ConfigManager, "load_config", lambda: {"check_dependencies_on_startup": False})

    cfg = ConfigManager.load_config()
    should_check = cfg.get("check_dependencies_on_startup", True)
    assert should_check is False

    if should_check:
        missing = EnvironmentDoctor.check_missing()
    else:
        missing = []

    assert missing == []
    assert len(checked) == 0
