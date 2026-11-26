"""
tests/unit/test_log_console.py
==============================
Модульные тесты для современной консоли логов и отладки (LogConsoleDialog):
- Инициализация FramelessDialog и FluentTitleBar
- Корректность расположения элементов интерфейса, кнопок, поиска и фильтрации
- Динамическая адаптация цветовой палитры под цвет активных обоев (Material You / Fluent Tint)
- Фильтрация логов по уровням (ALL, INFO, WARNING, ERROR, DEBUG)
- Поиск по тексту логов
- Копирование логов в буфер обмена и очистка сессионного журнала
- Закрытие по клавише Escape
"""

import os
from unittest.mock import MagicMock
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


class DummyLogWindow(QMainWindow):
    """Легковесный макет главного окна для тестирования консоли логов."""
    def __init__(self):
        super().__init__()
        self.background_pixmap = None


@pytest.fixture
def dummy_window(qapp):
    win = DummyLogWindow()
    yield win
    win.deleteLater()


def test_log_console_initialization(qapp, dummy_window):
    """Проверяет создание LogConsoleDialog, структуру FluentTitleBar и элементы интерфейса."""
    from ui.log_console import LogConsoleDialog

    dialog = LogConsoleDialog(dummy_window)
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert dialog.windowTitle() == "Консоль логов и отладки — NeonRoute"

    # Шапка FluentTitleBar: closeBtn видим, min/max скрыты
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # Элементы управления
    assert hasattr(dialog, "level_filter")
    assert hasattr(dialog, "search_box")
    assert hasattr(dialog, "text_edit")
    assert hasattr(dialog, "btn_copy")
    assert hasattr(dialog, "btn_clear")
    assert hasattr(dialog, "btn_export")
    assert hasattr(dialog, "btn_close")
    assert hasattr(dialog, "lbl_count")

    dialog.deleteLater()


def test_log_console_adaptive_palette_from_wallpaper(qapp, dummy_window):
    """Проверяет, что цвета консоли, терминала и кнопок гармонично адаптируются под цвет обоев."""
    from ui.log_console import LogConsoleDialog
    from ui.common.palette_utils import extract_adaptive_palette

    # 1. По умолчанию при отсутствии обоев
    default_pal = extract_adaptive_palette(None)
    dialog = LogConsoleDialog(dummy_window)
    dialog.show()

    assert dialog._current_palette["bg"] == default_pal["bg"]
    assert default_pal["btn"] in dialog.styleSheet()

    # 2. Устанавливаем фиолетовые обои (#A855F7)
    purple_pixmap = QPixmap(64, 64)
    purple_pixmap.fill(QColor("#A855F7"))
    dummy_window.background_pixmap = purple_pixmap

    dialog.update_adaptive_palette()

    # Палитра и стили кнопок должны перестроиться под оттенок обоев
    assert dialog._current_palette["bg"] != default_pal["bg"]
    assert dialog._current_palette["btn"] in dialog.styleSheet()
    assert dialog._current_palette["btn_hover"] in dialog.styleSheet()
    assert dialog._current_palette["terminal_bg"] in dialog.styleSheet()

    dialog.deleteLater()


def test_log_console_filter_and_search(qapp, dummy_window):
    """Проверяет фильтрацию записей по уровням и текстовый поиск."""
    from ui.log_console import LogConsoleDialog, _CURRENT_SESSION_LOGS

    _CURRENT_SESSION_LOGS.clear()
    _CURRENT_SESSION_LOGS.append(("[10:00:01] [INFO] [Core]: System ready", "INFO"))
    _CURRENT_SESSION_LOGS.append(("[10:00:02] [WARNING] [VRAM]: Memory low", "WARNING"))
    _CURRENT_SESSION_LOGS.append(("[10:00:03] [ERROR] [Network]: Timeout connecting", "ERROR"))
    _CURRENT_SESSION_LOGS.append(("[10:00:04] [DEBUG] [Cache]: KV budget computed", "DEBUG"))

    dialog = LogConsoleDialog(dummy_window)
    dialog.show()

    # Все 4 записи отображаются
    text = dialog.text_edit.toPlainText()
    assert "System ready" in text
    assert "Memory low" in text
    assert "Timeout connecting" in text
    assert "KV budget computed" in text

    # Фильтр по ERROR
    dialog._on_filter_changed("ERROR")
    text = dialog.text_edit.toPlainText()
    assert "Timeout connecting" in text
    assert "System ready" not in text
    assert "Memory low" not in text

    # Фильтр по INFO
    dialog._on_filter_changed("INFO")
    text = dialog.text_edit.toPlainText()
    assert "System ready" in text
    assert "Timeout connecting" not in text

    # Сброс на ALL и поиск по тексту
    dialog._on_filter_changed("ALL")
    dialog._on_search_changed("Memory")
    text = dialog.text_edit.toPlainText()
    assert "Memory low" in text
    assert "System ready" not in text
    assert "Timeout connecting" not in text

    dialog.deleteLater()


def test_log_console_copy_and_clear(qapp, dummy_window):
    """Проверяет копирование логов в буфер обмена и очистку журнала."""
    from ui.log_console import LogConsoleDialog, _CURRENT_SESSION_LOGS

    _CURRENT_SESSION_LOGS.clear()
    _CURRENT_SESSION_LOGS.append(("[12:00:00] [INFO] [Test]: Clipboard test log entry", "INFO"))

    dialog = LogConsoleDialog(dummy_window)
    dialog.show()

    # 1. Копирование в буфер
    dialog._copy_logs()
    clipboard_text = QApplication.clipboard().text()
    assert "Clipboard test log entry" in clipboard_text
    assert "скопированы" in dialog.status_lbl.text()

    # 2. Очистка
    dialog._clear_logs()
    assert len(_CURRENT_SESSION_LOGS) == 0
    assert dialog.text_edit.toPlainText() == ""
    assert "очищена" in dialog.status_lbl.text()

    dialog.deleteLater()


def test_log_console_escape_key_closes(qapp, dummy_window):
    """Проверяет закрытие диалога по нажатию клавиши Escape."""
    from ui.log_console import LogConsoleDialog

    dialog = LogConsoleDialog(dummy_window)
    dialog.show()
    assert dialog.isVisible() is True

    esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dialog.keyPressEvent(esc_event)

    assert dialog.isVisible() is False
    dialog.deleteLater()
