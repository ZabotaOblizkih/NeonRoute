"""
tests/unit/test_about_dialog.py
==============================
Модульные тесты для современного диалогового окна «О программе» (AboutDialog):
- Инициализация FramelessDialog и FluentTitleBar
- Корректность отображения кнопки закрытия [X] в правом верхнем углу
- Проверка элементов интерфейса (название, версия, разработчик, стек, железо, кнопка закрытия)
- Динамическая адаптация цветовой палитры под цвет активных обоев (Material You / Fluent Tint)
- Закрытие окна по нажатию клавиши Escape и по кнопке «Закрыть»
"""

import os
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


class DummyAboutParent(QMainWindow):
    """Легковесный родительский виджет с поддержкой фона для тестов."""
    def __init__(self):
        super().__init__()
        self.background_pixmap = None


@pytest.fixture
def dummy_window(qapp):
    win = DummyAboutParent()
    yield win
    win.deleteLater()


def test_about_dialog_initialization(qapp, dummy_window):
    """Проверяет создание AboutDialog, структуру FluentTitleBar и элементы интерфейса."""
    from ui.dialogs.about_dialog import AboutDialog
    from config import APP_NAME, APP_VERSION

    dialog = AboutDialog(dummy_window)
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert dialog.windowTitle() == f"О программе — {APP_NAME}"

    # Шапка FluentTitleBar: closeBtn видим, min/max скрыты
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # Элементы карточки и кнопки
    assert APP_NAME in dialog.title_lbl.text()
    assert APP_VERSION in dialog.title_lbl.text()
    assert "ZabotaOblizkih" in dialog.dev_val.text()
    assert hasattr(dialog, "btn_close")

    dialog.deleteLater()


def test_about_dialog_adaptive_palette_from_wallpaper(qapp, dummy_window):
    """Проверяет, что цвета окна «О программе» гармонично адаптируются под цвет обоев."""
    from ui.dialogs.about_dialog import AboutDialog
    from ui.common.palette_utils import extract_adaptive_palette

    # 1. По умолчанию при отсутствии обоев
    default_pal = extract_adaptive_palette(None)
    dialog = AboutDialog(dummy_window)
    dialog.show()

    assert dialog._current_palette["bg"] == default_pal["bg"]
    assert default_pal["btn"] in dialog.styleSheet()

    # 2. Устанавливаем изумрудно-зеленые обои (#10B981)
    green_pixmap = QPixmap(64, 64)
    green_pixmap.fill(QColor("#10B981"))
    dummy_window.background_pixmap = green_pixmap

    dialog.update_adaptive_palette()

    # Палитра и стили кнопок должны перестроиться под оттенок обоев
    assert dialog._current_palette["bg"] != default_pal["bg"]
    assert dialog._current_palette["btn"] in dialog.styleSheet()
    assert dialog._current_palette["btn_hover"] in dialog.styleSheet()
    assert dialog._current_palette["card"] in dialog.styleSheet()

    dialog.deleteLater()


def test_about_dialog_close_actions(qapp, dummy_window):
    """Проверяет закрытие диалога по кнопке «Закрыть» и по клавише Escape."""
    from ui.dialogs.about_dialog import AboutDialog

    # 1. Закрытие по Escape
    dialog1 = AboutDialog(dummy_window)
    dialog1.show()
    assert dialog1.isVisible() is True

    esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dialog1.keyPressEvent(esc_event)
    assert dialog1.isVisible() is False
    dialog1.deleteLater()

    # 2. Закрытие по кнопке btn_close
    dialog2 = AboutDialog(dummy_window)
    dialog2.show()
    assert dialog2.isVisible() is True

    dialog2.btn_close.click()
    assert dialog2.isVisible() is False
    dialog2.deleteLater()
