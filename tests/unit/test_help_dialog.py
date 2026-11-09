"""
tests/unit/test_help_dialog.py
==============================
Модульные тесты для диалогового окна «Горячие клавиши и руководство» (HelpDialog):
- Инициализация FramelessDialog и FluentTitleBar
- Корректность отображения кнопки закрытия [X] в правом верхнем углу
- Проверка элементов интерфейса (поисковая строка SearchLineEdit, карточки шорткатов, кнопка «Понятно»)
- Динамическая адаптация цветовой палитры под цвет активных обоев (Material You / Fluent Tint)
- Фильтрация карточек по поисковому запросу
- Закрытие окна по нажатию клавиши Escape и по кнопке «Понятно»
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


class DummyHelpParent(QMainWindow):
    """Легковесный родительский виджет с поддержкой фона для тестов."""
    def __init__(self):
        super().__init__()
        self.background_pixmap = None


@pytest.fixture
def dummy_window(qapp):
    win = DummyHelpParent()
    yield win
    win.deleteLater()


def test_help_dialog_initialization(qapp, dummy_window):
    """Проверяет создание HelpDialog, структуру FluentTitleBar и элементы интерфейса."""
    from ui.dialogs.help_dialog import HelpDialog

    dialog = HelpDialog(dummy_window)
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert "Горячие клавиши" in dialog.windowTitle()

    # Шапка FluentTitleBar: closeBtn видим, min/max скрыты
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # Элементы поиска, карточки и кнопки
    assert hasattr(dialog, "search_box")
    assert hasattr(dialog, "btn_close")
    assert len(dialog._cards) > 0

    dialog.deleteLater()


def test_help_dialog_adaptive_palette_from_wallpaper(qapp, dummy_window):
    """Проверяет, что цвета окна «Горячие клавиши» гармонично адаптируются под цвет обоев."""
    from ui.dialogs.help_dialog import HelpDialog
    from ui.common.palette_utils import extract_adaptive_palette

    # 1. По умолчанию при отсутствии обоев
    default_pal = extract_adaptive_palette(None)
    dialog = HelpDialog(dummy_window)
    dialog.show()

    assert dialog._current_palette["bg"] == default_pal["bg"]
    assert default_pal["btn"] in dialog.styleSheet()

    # 2. Устанавливаем янтарно-оранжевые обои (#F59E0B)
    amber_pixmap = QPixmap(64, 64)
    amber_pixmap.fill(QColor("#F59E0B"))
    dummy_window.background_pixmap = amber_pixmap

    dialog.update_adaptive_palette()

    # Палитра и стили кнопок должны перестроиться под оттенок обоев
    assert dialog._current_palette["bg"] != default_pal["bg"]
    assert dialog._current_palette["btn"] in dialog.styleSheet()
    assert dialog._current_palette["btn_hover"] in dialog.styleSheet()
    assert dialog._current_palette["card"] in dialog.styleSheet()

    dialog.deleteLater()


def test_help_dialog_search_filter(qapp, dummy_window):
    """Проверяет фильтрацию карточек горячих клавиш и руководства при поиске."""
    from ui.dialogs.help_dialog import HelpDialog

    dialog = HelpDialog(dummy_window)
    dialog.show()

    # Изначально все карточки видны
    assert all(card.isVisible() for card in dialog._cards)

    # Вводим редкий поисковый запрос (например, "Ctrl + B")
    dialog._on_search_changed("Ctrl + B")
    visible_cards = [c for c in dialog._cards if not c.isHidden()]
    assert len(visible_cards) >= 1
    assert len(visible_cards) < len(dialog._cards)

    # Сбрасываем поиск
    dialog._on_search_changed("")
    assert all(card.isVisible() for card in dialog._cards)

    dialog.deleteLater()


def test_help_dialog_close_actions(qapp, dummy_window):
    """Проверяет закрытие диалога по кнопке «Понятно» и по клавише Escape."""
    from ui.dialogs.help_dialog import HelpDialog

    # 1. Закрытие по Escape
    dialog1 = HelpDialog(dummy_window)
    dialog1.show()
    assert dialog1.isVisible() is True

    esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dialog1.keyPressEvent(esc_event)
    assert dialog1.isVisible() is False
    dialog1.deleteLater()

    # 2. Закрытие по кнопке btn_close
    dialog2 = HelpDialog(dummy_window)
    dialog2.show()
    assert dialog2.isVisible() is True

    dialog2.btn_close.click()
    assert dialog2.isVisible() is False
    dialog2.deleteLater()
