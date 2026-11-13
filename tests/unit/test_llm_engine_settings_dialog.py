"""
tests/unit/test_llm_engine_settings_dialog.py
=============================================
Модульные тесты для современного диалогового окна «Настройки движка LLM» (LLMEngineSettingsDialog):
- Инициализация FramelessDialog и FluentTitleBar
- Корректность отображения кнопки закрытия [X] в правом верхнем углу (minBtn/maxBtn скрыты)
- Проверка элементов интерфейса (карточка GPU, селектор KV-кэша, реактивная подсказка, свитч Flash Attention, кнопки)
- Реактивное обновление карточки-подсказки при смене типа KV-кэша (Q8_0, Q4_0, FP16)
- Динамическая адаптация цветовой палитры под цвет активных обоев (Material You / Fluent Tint)
- Сохранение настроек в config.json и мягкий перезапуск llama-server
- Закрытие окна по нажатию клавиши Escape и по кнопке «Отмена»
"""

import os
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
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


class DummyLLMEngineParent(QMainWindow):
    """Легковесный родительский виджет с поддержкой фона и ProcessManager для тестов."""
    def __init__(self):
        super().__init__()
        self.background_pixmap = None
        self.process_manager = MagicMock()
        self.process_manager.is_lm_studio_running.return_value = True
        self.process_manager.restart_llama_server_async = MagicMock()


@pytest.fixture
def dummy_window(qapp):
    win = DummyLLMEngineParent()
    yield win
    win.deleteLater()


def test_llm_engine_dialog_initialization(qapp, dummy_window):
    """Проверяет создание LLMEngineSettingsDialog, FluentTitleBar и элементы интерфейса."""
    from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog

    dialog = LLMEngineSettingsDialog(parent=dummy_window)
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert "Настройки движка LLM" in dialog.windowTitle()

    # Шапка FluentTitleBar: closeBtn видим, min/max скрыты
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # Элементы интерфейса
    assert hasattr(dialog, "hw_card")
    assert hasattr(dialog, "kv_combo")
    assert hasattr(dialog, "hint_card")
    assert hasattr(dialog, "hint_title_lbl")
    assert hasattr(dialog, "hint_budget_lbl")
    assert hasattr(dialog, "hint_effect_lbl")
    assert hasattr(dialog, "hint_side_lbl")
    assert hasattr(dialog, "fa_frame")
    assert hasattr(dialog, "switch_fa")
    assert hasattr(dialog, "thinking_frame")
    assert hasattr(dialog, "segmented_thinking")
    assert hasattr(dialog, "lbl_thinking_hint")
    assert hasattr(dialog, "btn_cancel")
    assert hasattr(dialog, "btn_save")

    # Проверка начальных значений
    assert dialog.kv_combo.count() == 3
    assert dialog.switch_fa.onText == "ВКЛ"
    assert dialog.switch_fa.offText == "ВЫКЛ"
    assert dialog.current_thinking in ("auto", "on", "off")

    dialog.deleteLater()


def test_escape_key_closes_llm_engine_dialog(qapp, dummy_window):
    """Проверяет отклонение (reject) диалога по нажатию клавиши Escape."""
    from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog

    dialog = LLMEngineSettingsDialog(parent=dummy_window)
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


def test_adaptive_palette_generation_llm_engine(qapp, dummy_window):
    """Проверяет генерацию адаптивной палитры под цвет обоев."""
    from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(14, 165, 233))  # Голубой тон
    dummy_window.background_pixmap = pixmap

    dialog = LLMEngineSettingsDialog(parent=dummy_window)
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

    dialog.deleteLater()


def test_kv_selection_reactive_hints(qapp, dummy_window):
    """Проверяет реактивное обновление карточки-подсказки при переключении типа KV-кэша."""
    from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog

    dialog = LLMEngineSettingsDialog(parent=dummy_window)

    # Выбираем Q4_0 (индекс 1)
    dialog.kv_combo.setCurrentIndex(1)
    assert dialog.current_kv == "q4_0"
    assert "Q4_0" in dialog.hint_title_lbl.text()
    assert "75%" in dialog.hint_budget_lbl.text()

    # Выбираем FP16 (индекс 2)
    dialog.kv_combo.setCurrentIndex(2)
    assert dialog.current_kv == "f16"
    assert "FP16" in dialog.hint_title_lbl.text()
    assert "0%" in dialog.hint_budget_lbl.text()

    # Выбираем Q8_0 (индекс 0)
    dialog.kv_combo.setCurrentIndex(0)
    assert dialog.current_kv == "q8_0"
    assert "Q8_0" in dialog.hint_title_lbl.text()
    assert "50%" in dialog.hint_budget_lbl.text()

    dialog.deleteLater()


def test_save_clicked_persists_config_and_triggers_restart(qapp, dummy_window):
    """Проверяет сохранение настроек в config.json и мягкий перезапуск сервера."""
    from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog

    dialog = LLMEngineSettingsDialog(parent=dummy_window)

    # Выбираем Q4_0 и отключаем Flash Attention
    dialog.kv_combo.setCurrentIndex(1)
    dialog.switch_fa.setChecked(False)

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
    assert saved_config.get("kv_cache_type") == "q4_0"
    assert saved_config.get("flash_attention_enabled") is False

    # Проверка перезапуска llama-server через ProcessManager родительского окна
    assert dummy_window.process_manager.restart_llama_server_async.called

    dialog.deleteLater()


def test_thinking_mode_reactive_hints(qapp, dummy_window):
    """Проверяет реактивное переключение режимов Thinking и обновление текста подсказок."""
    from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog

    dialog = LLMEngineSettingsDialog(parent=dummy_window)

    # 1. Режим 'on'
    dialog.segmented_thinking.setCurrentItem("on")
    assert dialog.current_thinking == "on"
    assert "всегда рассуждает" in dialog.lbl_thinking_hint.text()

    # 2. Режим 'off'
    dialog.segmented_thinking.setCurrentItem("off")
    assert dialog.current_thinking == "off"
    assert "Мгновенные ответы" in dialog.lbl_thinking_hint.text()

    # 3. Режим 'auto'
    dialog.segmented_thinking.setCurrentItem("auto")
    assert dialog.current_thinking == "auto"
    assert "Инженера" in dialog.lbl_thinking_hint.text()

    dialog.deleteLater()


def test_save_clicked_only_thinking_mode_does_not_restart_server(qapp, dummy_window):
    """Проверяет, что при изменении ТОЛЬКО thinking_mode перезапуск сервера НЕ вызывается."""
    from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog

    dummy_window.process_manager.restart_llama_server_async.reset_mock()
    dialog = LLMEngineSettingsDialog(parent=dummy_window)

    # Не меняем kv и fa, меняем только thinking_mode
    new_thinking = "off" if dialog.current_thinking != "off" else "on"
    dialog.segmented_thinking.setCurrentItem(new_thinking)

    saved_config = {
        "kv_cache_type": dialog.current_kv,
        "flash_attention_enabled": dialog.current_fa,
    }

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
    assert saved_config.get("thinking_mode") == new_thinking
    # restart_llama_server_async НЕ должен вызываться, так как kv и fa не менялись
    assert not dummy_window.process_manager.restart_llama_server_async.called

    dialog.deleteLater()

