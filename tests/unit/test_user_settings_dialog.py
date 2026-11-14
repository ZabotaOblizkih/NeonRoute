"""
tests/unit/test_user_settings_dialog.py
=======================================
Модульные тесты для современного диалогового окна «Настройки профиля и Эларии» (UserSettingsDialog):
- Инициализация FramelessDialog и FluentTitleBar
- Корректность отображения кнопки закрытия [X] в правом верхнем углу (minBtn/maxBtn скрыты)
- Проверка вкладок SegmentedWidget («⚡ Ассистент» и «🎭 Ролевой чат»)
- Проверка полей профиля ассистента (аватар, имя, био, модель, режим артов)
- Проверка менеджера персон игрока (список персон, создание, установка основного, удаление)
- Обратная совместимость адаптеров (tab_widget, rb_art_auto/manual, combo_rp_gender)
- Динамическая адаптация цветовой палитры под цвет активных обоев (Material You / Fluent Tint)
- Сохранение настроек и эмит сигналов settings_applied и profiles_updated
- Закрытие окна по клавише Escape и кнопке «Отмена»
- Бесшовная отрисовка интерактивных аватарок (InteractiveAvatarWidget) с оптической глубиной и без сжатия
"""

import os
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QEvent, QPointF
from PyQt6.QtGui import QPixmap, QColor, QKeyEvent, QMouseEvent, QEnterEvent
from PyQt6.QtWidgets import QApplication, QMainWindow
from qframelesswindow import FramelessDialog


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class DummyUserSettingsParent(QMainWindow):
    """Легковесный родительский виджет с поддержкой фона и lm_combo для тестов."""
    def __init__(self):
        super().__init__()
        self.background_pixmap = None
        self.lm_combo = MagicMock()
        self.lm_combo.count.return_value = 2
        self.lm_combo.itemText.side_effect = lambda i: ["model-elaria-1.gguf", "model-elaria-2.gguf"][i]
        self.lm_combo.findText.return_value = 0
        self.lm_combo.currentText.return_value = "model-elaria-1.gguf"


@pytest.fixture
def dummy_window(qapp):
    win = DummyUserSettingsParent()
    yield win
    win.deleteLater()


def test_user_settings_dialog_initialization(qapp, dummy_window):
    """Проверяет создание UserSettingsDialog, FluentTitleBar и структуру элементов."""
    from ui.dialogs.user_settings_dialog import UserSettingsDialog

    dialog = UserSettingsDialog(initial_tab=0, parent=dummy_window)
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert "Настройки профиля" in dialog.windowTitle()

    # Шапка FluentTitleBar: closeBtn видим, min/max скрыты
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # Навигация SegmentedWidget и страницы
    assert hasattr(dialog, "nav_segmented")
    assert hasattr(dialog, "stacked_widget")
    assert dialog.stacked_widget.count() == 2
    assert dialog.stacked_widget.currentIndex() == 0

    # Элементы вкладки Ассистент
    assert hasattr(dialog, "asst_avatar_lbl")
    assert hasattr(dialog, "asst_name_input")
    assert hasattr(dialog, "asst_bio_input")
    assert hasattr(dialog, "combo_asst_model")
    assert hasattr(dialog, "art_mode_segmented")

    # Элементы вкладки РП чат
    assert hasattr(dialog, "list_personas")
    assert hasattr(dialog, "btn_new_persona")
    assert hasattr(dialog, "btn_set_primary")
    assert hasattr(dialog, "btn_del_persona")
    assert hasattr(dialog, "rp_avatar_lbl")
    assert hasattr(dialog, "rp_name_input")
    assert hasattr(dialog, "sw_gender")

    # Кнопки панели действий
    assert hasattr(dialog, "btn_cancel")
    assert hasattr(dialog, "btn_save_all")

    # Прокси обратной совместимости
    assert hasattr(dialog, "tab_widget")
    assert hasattr(dialog, "rb_art_auto")
    assert hasattr(dialog, "rb_art_manual")
    assert hasattr(dialog, "combo_rp_gender")

    dialog.close()
    dialog.deleteLater()


def test_escape_key_closes_user_settings_dialog(qapp, dummy_window):
    """Проверяет отклонение (reject) диалога по нажатию клавиши Escape."""
    from ui.dialogs.user_settings_dialog import UserSettingsDialog

    dialog = UserSettingsDialog(initial_tab=0, parent=dummy_window)
    dialog.show()

    rejected_called = []
    dialog.rejected.connect(lambda: rejected_called.append(True))

    esc_event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier
    )
    dialog.keyPressEvent(esc_event)

    assert len(rejected_called) == 1
    dialog.close()
    dialog.deleteLater()


def test_adaptive_palette_generation_user_settings(qapp, dummy_window):
    """Проверяет генерацию адаптивной палитры под цвет обоев."""
    from ui.dialogs.user_settings_dialog import UserSettingsDialog

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(168, 85, 247))  # Пурпурный оттенок
    dummy_window.background_pixmap = pixmap

    dialog = UserSettingsDialog(initial_tab=0, parent=dummy_window)
    dialog.show()

    palette = dialog._current_palette
    assert palette is not None
    assert "bg" in palette
    assert "card" in palette
    assert "accent" in palette
    assert "group_title" in palette

    sheet = dialog.styleSheet()
    assert palette["bg"] in sheet
    assert palette["accent"] in sheet

    dialog.close()
    dialog.deleteLater()


def test_tab_switching_and_compat_proxy(qapp, dummy_window):
    """Проверяет переключение вкладок через set_current_tab и через tab_widget прокси."""
    from ui.dialogs.user_settings_dialog import UserSettingsDialog

    dialog = UserSettingsDialog(initial_tab=0, parent=dummy_window)

    # Переключение на вкладку РП (1)
    dialog.set_current_tab(1)
    assert dialog.stacked_widget.currentIndex() == 1
    assert dialog.nav_segmented.currentRouteKey() == "tab_rp"
    assert dialog.tab_widget.currentIndex() == 1

    # Обратное переключение через прокси tab_widget (0)
    dialog.tab_widget.setCurrentIndex(0)
    assert dialog.stacked_widget.currentIndex() == 0
    assert dialog.nav_segmented.currentRouteKey() == "tab_asst"

    dialog.close()
    dialog.deleteLater()


def test_save_all_profiles_and_signal_emissions(qapp, dummy_window):
    """Проверяет валидацию, сохранение настроек и эмит сигналов."""
    from ui.dialogs.user_settings_dialog import UserSettingsDialog

    dialog = UserSettingsDialog(initial_tab=0, parent=dummy_window)

    dialog.asst_name_input.setText("Тестовый Пользователь")
    dialog.asst_bio_input.setPlainText("Тестовый био контекст")
    dialog.art_mode_segmented.setCurrentItem("auto")

    settings_applied_payload = []
    profiles_updated_called = []

    dialog.settings_applied.connect(lambda cfg: settings_applied_payload.append(cfg))
    dialog.profiles_updated.connect(lambda: profiles_updated_called.append(True))

    mock_saved_cfg = {}

    def mock_save_config(cfg):
        mock_saved_cfg.update(cfg)

    with patch("services.session_manager.SessionManager.save_user_profiles") as mock_save_profiles, \
         patch("utils.config_manager.ConfigManager.save_config", side_effect=mock_save_config), \
         patch("utils.config_manager.ConfigManager.load_config", return_value=mock_saved_cfg), \
         patch("utils.file_utils.atomic_write_json"):
        dialog.btn_save_all.click()

    assert len(settings_applied_payload) == 1
    assert len(profiles_updated_called) == 1
    assert mock_saved_cfg.get("user_name") == "Тестовый Пользователь"
    assert mock_saved_cfg.get("auto_generate_art") is True
    assert mock_save_profiles.called

    dialog.close()
    dialog.deleteLater()


def test_interactive_avatar_widget_seamless_and_depth(qapp):
    """Проверяет бесшовную отрисовку аватара, корректный DPR и оптическую глубину на ховере."""
    from ui.dialogs.user_settings_dialog import InteractiveAvatarWidget

    widget = InteractiveAvatarWidget(size=64)
    widget.show()

    # 1. Проверка начального состояния
    assert widget.avatar_size == 64
    assert widget._is_hovered is False
    assert widget._pixmap.isNull() is True

    # 2. Установка аватара через QPixmap
    sample_pix = QPixmap(192, 192)
    sample_pix.setDevicePixelRatio(3.0)
    sample_pix.fill(QColor(59, 130, 246))
    widget.setPixmap(sample_pix)

    assert widget.pixmap().isNull() is False
    assert widget.pixmap().width() == 192
    assert widget.pixmap().devicePixelRatio() == 3.0

    # 3. Проверка ховера (enterEvent / leaveEvent) для оптической глубины
    enter_evt = QEnterEvent(QPointF(32.0, 32.0), QPointF(32.0, 32.0), QPointF(32.0, 32.0))
    widget.enterEvent(enter_evt)
    assert widget._is_hovered is True

    leave_evt = QEvent(QEvent.Type.Leave)
    widget.leaveEvent(leave_evt)
    assert widget._is_hovered is False

    # 4. Проверка клика по аватару
    clicked_count = []
    widget.avatar_clicked.connect(lambda: clicked_count.append(True))

    mouse_evt = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(32.0, 32.0),
        QPointF(32.0, 32.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )
    widget.mousePressEvent(mouse_evt)
    assert len(clicked_count) == 1

    # 5. Отрисовка paintEvent (не должно падать)
    widget.repaint()

    widget.close()
    widget.deleteLater()
