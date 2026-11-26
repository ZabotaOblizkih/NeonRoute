"""
tests/unit/test_mode_model_settings_dialog.py
=============================================
Модульные тесты для современного диалогового окна «Выбор моделей по режимам»
(ModeModelSettingsDialog / EngineerSettingsDialog):
- Инициализация FramelessDialog и FluentTitleBar
- Корректность отображения кнопки закрытия [X] в правом верхнем углу (minBtn/maxBtn скрыты)
- Проверка элементов интерфейса (3 карточки: Ассистент, РП Чат, Инженер; ComboBox'ы; кнопки)
- Обратная совместимость алиасов (ModeModelSettingsDialog, combo_models)
- Динамическая адаптация цветовой палитры под цвет активных обоев (Material You / Fluent Tint)
- Заполнение и выбор моделей (_populate_combo, _on_refresh_clicked)
- Сохранение настроек и hot-swap (_on_save_clicked)
- Закрытие окна по нажатию клавиши Escape и по кнопке «Отмена»
"""

import os
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QColor, QKeyEvent
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget
from qframelesswindow import FramelessDialog


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class DummyParentWindow(QMainWindow):
    """Легковесный родительский виджет с поддержкой фона и компонентов для тестов."""
    def __init__(self):
        super().__init__()
        self.background_pixmap = None
        self.app_config = {}
        self.active_lms_model = ""
        self.current_session = {"mode": "assistant", "model_id": ""}
        self.lm_combo = MagicMock()
        self.lm_combo.count.return_value = 2
        self.lm_combo.itemText.side_effect = lambda i: ["model-alpha.gguf", "model-beta.gguf"][i]
        self.lm_combo.findText.return_value = 0
        self.lm_client = MagicMock()
        self.lm_client.get_models.return_value = ["model-alpha.gguf", "model-beta.gguf", "model-gamma.gguf"]
        self.lm_client._last_loaded_model_id = "model-alpha.gguf"
        self._save_current_session = MagicMock()


@pytest.fixture
def dummy_window(qapp):
    win = DummyParentWindow()
    yield win
    win.deleteLater()


def test_dialog_initialization(qapp, dummy_window):
    """Проверяет создание ModeModelSettingsDialog, FluentTitleBar и элементы интерфейса."""
    from ui.dialogs.engineer_settings_dialog import ModeModelSettingsDialog, EngineerSettingsDialog

    assert ModeModelSettingsDialog is EngineerSettingsDialog

    dialog = ModeModelSettingsDialog(parent=dummy_window)
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert "Выбор моделей по режимам" in dialog.windowTitle()

    # Шапка FluentTitleBar: closeBtn видим, min/max скрыты
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # Элементы карточек режимов
    assert hasattr(dialog, "lbl_title")
    assert hasattr(dialog, "lbl_desc")
    assert hasattr(dialog, "card_asst")
    assert hasattr(dialog, "card_rp")
    assert hasattr(dialog, "card_eng")
    assert hasattr(dialog, "combo_assistant")
    assert hasattr(dialog, "combo_rp")
    assert hasattr(dialog, "combo_engineer")
    assert dialog.combo_models is dialog.combo_engineer

    # Кнопки
    assert hasattr(dialog, "btn_refresh")
    assert hasattr(dialog, "btn_cancel")
    assert hasattr(dialog, "btn_save")

    dialog.deleteLater()


def test_escape_key_closes_dialog(qapp, dummy_window):
    """Проверяет отклонение (reject) диалога по нажатию клавиши Escape."""
    from ui.dialogs.engineer_settings_dialog import ModeModelSettingsDialog

    dialog = ModeModelSettingsDialog(parent=dummy_window)
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
    dialog.deleteLater()


def test_adaptive_palette_generation(qapp, dummy_window):
    """Проверяет генерацию адаптивной палитры под цвет обоев."""
    from ui.dialogs.engineer_settings_dialog import ModeModelSettingsDialog

    # Создаем тестовый пиксмап изумрудного цвета
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(16, 185, 129))
    dummy_window.background_pixmap = pixmap

    dialog = ModeModelSettingsDialog(parent=dummy_window)
    dialog.show()

    palette = dialog._current_palette
    assert palette is not None
    assert "bg" in palette
    assert "card" in palette
    assert "accent" in palette
    assert "group_title" in palette

    # Проверяем применение цветов в stylesheet
    sheet = dialog.styleSheet()
    assert palette["bg"] in sheet
    assert palette["accent"] in sheet

    dialog.deleteLater()


def test_populate_and_refresh_combos(qapp, dummy_window):
    """Проверяет заполнение выпадающих списков и работу кнопки обновления."""
    from ui.dialogs.engineer_settings_dialog import ModeModelSettingsDialog

    dialog = ModeModelSettingsDialog(parent=dummy_window)

    models = ["model-1.gguf", "model-2.gguf", "model-3.gguf"]
    dialog._populate_combo(dialog.combo_assistant, "model-2.gguf", models)

    assert dialog.combo_assistant.count() == 3
    assert dialog.combo_assistant.currentText() == "model-2.gguf"

    # Проверка добавления отсутствующей в списке модели
    dialog._populate_combo(dialog.combo_rp, "custom-model.gguf", models)
    assert dialog.combo_rp.count() == 4
    assert dialog.combo_rp.currentText() == "custom-model.gguf"

    # Клик по кнопке обновления списка моделей
    with patch.object(dialog, "_get_available_models", return_value=["model-1.gguf", "model-2.gguf"]):
        dialog.btn_refresh.click()
        assert dialog.combo_assistant.currentText() == "model-2.gguf"

    dialog.deleteLater()


def test_save_clicked_persists_config_and_triggers_hot_swap(qapp, dummy_window):
    """Проверяет сохранение настроек в config.json и синхронизацию с родителем."""
    from ui.dialogs.engineer_settings_dialog import ModeModelSettingsDialog

    dialog = ModeModelSettingsDialog(parent=dummy_window)

    dialog.combo_assistant.addItem("asst-v1.gguf")
    dialog.combo_assistant.setCurrentIndex(dialog.combo_assistant.count() - 1)

    dialog.combo_rp.addItem("rp-v1.gguf")
    dialog.combo_rp.setCurrentIndex(dialog.combo_rp.count() - 1)

    dialog.combo_engineer.addItem("eng-v1.gguf")
    dialog.combo_engineer.setCurrentIndex(dialog.combo_engineer.count() - 1)

    saved_config = {}

    def mock_save(cfg):
        saved_config.update(cfg)

    def mock_load():
        return dict(saved_config)

    accepted_called = []
    dialog.accepted.connect(lambda: accepted_called.append(True))

    with patch("utils.config_manager.ConfigManager.save_config", side_effect=mock_save), \
         patch("utils.config_manager.ConfigManager.load_config", side_effect=mock_load):
        dialog.btn_save.click()

    assert len(accepted_called) == 1
    assert saved_config.get("assistant_model_name") == "asst-v1.gguf"
    assert saved_config.get("rp_model_name") == "rp-v1.gguf"
    assert saved_config.get("engineer_model_name") == "eng-v1.gguf"
    assert saved_config.get("engineer_model") == "eng-v1.gguf"

    # Синхронизация с dummy_window
    assert dummy_window.app_config.get("assistant_model_name") == "asst-v1.gguf"
    assert dummy_window.active_lms_model == "asst-v1.gguf"
    assert dummy_window.current_session.get("model_id") == "asst-v1.gguf"
    assert dummy_window._save_current_session.called

    dialog.deleteLater()
