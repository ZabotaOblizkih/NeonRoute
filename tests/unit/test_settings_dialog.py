"""
tests/unit/test_settings_dialog.py
==================================
TDD-тестовый сьют для нового современного окна «Параметры и настройки» (SettingsDialog)
в стиле PyQt-Fluent-Widgets (qfluentwidgets):
- Инициализация SettingsDialog с FramelessTitleBar, скругленными углами и светлой/мягкой темой
- Навигация по 4 категориям: Система, Оформление, Диалоги, Справка
- Переключение страниц QStackedWidget при клике по элементам навигации
- Проверка делегирования всех карточек действий раздела «Система»
- Проверка делегирования раздела «Оформление» (обои, сброс темы, масштабирование шрифта, логи)
- Проверка раздела «Диалоги» (новый диалог, сэмплирование, медиа, папка истории, тумблер авто-арта, экспорт)
- Проверка раздела «Справка» (компоненты, горячие клавиши, о программе, выход)
- Проверка вызова SettingsDialog из SidebarWidget (кнопка-шестеренка btn_settings)
- Проверка сохранения глобальных горячих клавиш (MenuBuilder.setup_shortcuts)
"""

import os
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class DummyMainWindow(QMainWindow):
    """Изолированный легковесный макет MainWindow для контрактного тестирования SettingsDialog."""

    def __init__(self):
        super().__init__()
        self.app_config = {"auto_generate_art": False}
        self.chat_font_size = 13

        # Моки действий системы
        self._open_user_settings_dialog = MagicMock()
        self._open_knowledge_manager_dialog = MagicMock()
        self._open_engineer_skills_dialog = MagicMock()
        self._open_characters_gallery_dialog = MagicMock()
        self._open_mode_model_settings_dialog = MagicMock()
        self._open_llm_engine_settings_dialog = MagicMock()
        self._open_models_manager_dialog = MagicMock()
        self._open_clean_cache_dialog = MagicMock()

        # Моки действий оформления
        self._open_background_settings_dialog = MagicMock()
        self._on_background_reset = MagicMock()
        self._change_chat_font_size = MagicMock(side_effect=self._mock_change_font_size)
        self._reset_chat_font_size = MagicMock(side_effect=self._mock_reset_font_size)
        self._open_log_console_dialog = MagicMock()

        # Моки действий диалогов
        self._on_new_chat_clicked = MagicMock()
        self._open_global_model_sampling_dialog = MagicMock()
        self._open_chat_media_gallery = MagicMock()
        self._on_action_export_clicked = MagicMock()

        # Моки действий справки
        self.open_dependency_dialog = MagicMock()
        self._open_help_dialog = MagicMock()
        self._open_about_dialog = MagicMock()
        self.close = MagicMock()

        # Дополнительные методы MainWindow для совместимости с SidebarWidget и MenuBuilder
        self._open_campaign_info_dialog = MagicMock()
        self._show_search_bar = MagicMock()
        self._on_chat_list_item_clicked = MagicMock()
        self.statusBar = MagicMock(return_value=MagicMock())

    def _mock_change_font_size(self, delta: int):
        self.chat_font_size += delta

    def _mock_reset_font_size(self):
        self.chat_font_size = 13


@pytest.fixture
def dummy_window(qapp):
    win = DummyMainWindow()
    yield win
    win.deleteLater()


# =============================================================================
# 1. Инициализация и базовая структура
# =============================================================================

def test_settings_dialog_initialization(qapp, dummy_window):
    """Проверяет, что SettingsDialog корректно создается с заголовком и структурой."""
    from ui.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog(dummy_window)
    assert dialog is not None
    assert "Параметры и настройки" in dialog.windowTitle()
    assert hasattr(dialog, "stacked_widget")
    assert hasattr(dialog, "nav_panel")
    assert dialog.stacked_widget.count() >= 4


# =============================================================================
# 2. Навигация и переключение страниц
# =============================================================================

def test_settings_dialog_navigation_switching(qapp, dummy_window):
    """Проверяет плавное переключение между категориями настроек."""
    from ui.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog(dummy_window)

    # По умолчанию открыта первая страница (Система)
    assert dialog.stacked_widget.currentIndex() == 0

    # Переключение на страницу Оформление (индекс 1)
    dialog.switch_to_page(1)
    assert dialog.stacked_widget.currentIndex() == 1

    # Переключение на страницу Диалоги (индекс 2)
    dialog.switch_to_page(2)
    assert dialog.stacked_widget.currentIndex() == 2

    # Переключение на страницу Справка (индекс 3)
    dialog.switch_to_page(3)
    assert dialog.stacked_widget.currentIndex() == 3


# =============================================================================
# 3. Делегирование раздела «Система»
# =============================================================================

def test_system_page_actions_delegation(qapp, dummy_window):
    """Проверяет, что все кнопки/карточки раздела 'Система' корректно вызывают методы окна."""
    from ui.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog(dummy_window)

    # 1. Настройки Эларии
    assert hasattr(dialog, "card_asst_settings")
    dialog.card_asst_settings.clicked.emit()
    dummy_window._open_user_settings_dialog.assert_called_once_with(0)

    # 2. База знаний
    assert hasattr(dialog, "card_knowledge")
    dialog.card_knowledge.clicked.emit()
    dummy_window._open_knowledge_manager_dialog.assert_called_once()

    # 3. Знания инженера
    assert hasattr(dialog, "card_engineer_skills")
    dialog.card_engineer_skills.clicked.emit()
    dummy_window._open_engineer_skills_dialog.assert_called_once()

    # 4. Галерея персонажей
    assert hasattr(dialog, "card_gallery")
    dialog.card_gallery.clicked.emit()
    dummy_window._open_characters_gallery_dialog.assert_called_once()

    # 5. Выбор моделей по режимам
    assert hasattr(dialog, "card_engineer_settings")
    dialog.card_engineer_settings.clicked.emit()
    dummy_window._open_mode_model_settings_dialog.assert_called_once()

    # 6. Настройки движка LLM
    assert hasattr(dialog, "card_llm_engine")
    dialog.card_llm_engine.clicked.emit()
    dummy_window._open_llm_engine_settings_dialog.assert_called_once()

    # 7. Модели генерации
    assert hasattr(dialog, "card_models_manager")
    dialog.card_models_manager.clicked.emit()
    dummy_window._open_models_manager_dialog.assert_called_once()

    # 8. Очистка кэша
    assert hasattr(dialog, "card_clean_cache")
    dialog.card_clean_cache.clicked.emit()
    dummy_window._open_clean_cache_dialog.assert_called_once()


# =============================================================================
# 4. Делегирование раздела «Оформление»
# =============================================================================

def test_appearance_page_actions_delegation(qapp, dummy_window):
    """Проверяет карточки и кнопки раздела 'Оформление'."""
    from ui.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog(dummy_window)

    # 1. Обои
    assert hasattr(dialog, "card_bg")
    dialog.card_bg.clicked.emit()
    dummy_window._open_background_settings_dialog.assert_called_once()

    # 2. Затемнение
    assert hasattr(dialog, "card_dim")
    dialog.card_dim.clicked.emit()
    assert dummy_window._open_background_settings_dialog.call_count == 2

    # 3. Сброс темы
    assert hasattr(dialog, "card_reset_theme")
    dialog.card_reset_theme.clicked.emit()
    dummy_window._on_background_reset.assert_called_once()

    # 4. Масштаб шрифта
    assert hasattr(dialog, "btn_zoom_in")
    assert hasattr(dialog, "btn_zoom_out")
    assert hasattr(dialog, "btn_zoom_reset")

    dialog.btn_zoom_in.clicked.emit()
    dummy_window._change_chat_font_size.assert_called_with(1)

    dialog.btn_zoom_out.clicked.emit()
    dummy_window._change_chat_font_size.assert_called_with(-1)

    dialog.btn_zoom_reset.clicked.emit()
    dummy_window._reset_chat_font_size.assert_called_once()


# =============================================================================
# 5. Делегирование раздела «Диалоги»
# =============================================================================

def test_dialogs_page_actions_delegation(qapp, dummy_window, monkeypatch):
    """Проверяет настройки диалогов и переключатель авто-арта."""
    from ui.dialogs.settings_dialog import SettingsDialog

    # Мокаем ConfigManager.save_config и открытие папки
    mock_save_config = MagicMock()
    monkeypatch.setattr("utils.config_manager.ConfigManager.save_config", mock_save_config)

    dialog = SettingsDialog(dummy_window)

    # 1. Новый диалог
    assert hasattr(dialog, "card_new_chat")
    dialog.card_new_chat.clicked.emit()
    dummy_window._on_new_chat_clicked.assert_called_once()

    # 2. Сэмплирование модели
    assert hasattr(dialog, "card_sampling")
    dialog.card_sampling.clicked.emit()
    dummy_window._open_global_model_sampling_dialog.assert_called_once()

    # 3. Медиа галерея
    assert hasattr(dialog, "card_media_gallery")
    dialog.card_media_gallery.clicked.emit()
    dummy_window._open_chat_media_gallery.assert_called_once()

    # 4. Тумблер автоматической генерации иллюстраций
    assert hasattr(dialog, "switch_auto_art")
    assert dialog.switch_auto_art.isChecked() is False

    # Переключаем тумблер
    dialog.switch_auto_art.setChecked(True)
    assert dummy_window.app_config["auto_generate_art"] is True
    mock_save_config.assert_called_with(dummy_window.app_config)

    # 5. Экспорт текущей истории
    assert hasattr(dialog, "card_export")
    dialog.card_export.clicked.emit()
    dummy_window._on_action_export_clicked.assert_called_once()


# =============================================================================
# 6. Делегирование раздела «Справка» и Выход
# =============================================================================

def test_help_page_actions_delegation(qapp, dummy_window):
    """Проверяет карточки раздела 'Справка' и кнопку выхода."""
    from ui.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog(dummy_window)

    # 1. Проверка компонентов
    assert hasattr(dialog, "card_check_deps")
    dialog.card_check_deps.clicked.emit()
    dummy_window.open_dependency_dialog.assert_called_once()

    # 2. Консоль логов и отладки (в разделе «Справка»)
    assert hasattr(dialog, "card_log_console")
    dialog.card_log_console.clicked.emit()
    dummy_window._open_log_console_dialog.assert_called_once()

    # 3. Горячие клавиши
    assert hasattr(dialog, "card_help")
    dialog.card_help.clicked.emit()
    dummy_window._open_help_dialog.assert_called_once()

    # 4. О программе
    assert hasattr(dialog, "card_about")
    dialog.card_about.clicked.emit()
    dummy_window._open_about_dialog.assert_called_once()

    # 5. Выход
    assert hasattr(dialog, "card_exit")
    dialog.card_exit.clicked.emit()
    dummy_window.close.assert_called_once()


# =============================================================================
# 7. Интеграция с SidebarWidget (кнопка-шестеренка)
# =============================================================================

def test_sidebar_settings_button_triggers_settings_dialog(qapp, dummy_window, monkeypatch):
    """Проверяет, что кнопка-шестеренка в сайдбаре инициирует открытие нового SettingsDialog."""
    from ui.components.sidebar_widget import SidebarWidget

    dummy_window._open_main_settings_menu = MagicMock()
    sidebar = SidebarWidget(window=dummy_window)

    assert hasattr(sidebar, "btn_settings")
    # Эмулируем клик по шестеренке
    sidebar.btn_settings.click()
    dummy_window._open_main_settings_menu.assert_called_once_with(sidebar.btn_settings)


# =============================================================================
# 8. Неизменность глобальных горячих клавиш (Regression Guard)
# =============================================================================

def test_menu_shortcuts_integrity_regression(qapp, dummy_window):
    """Гарантирует, что глобальные хоткеи (Ctrl+N, Ctrl+Q, Ctrl+F, масштаб) сохранены."""
    from ui.builders.menu_builder import MenuBuilder

    MenuBuilder.setup_shortcuts(dummy_window)

    assert hasattr(dummy_window, "sc_new_chat")
    assert hasattr(dummy_window, "sc_exit")
    assert hasattr(dummy_window, "sc_search")
    assert hasattr(dummy_window, "sc_zoom_in_eq")
    assert hasattr(dummy_window, "sc_zoom_in_plus")
    assert hasattr(dummy_window, "sc_zoom_out")
    assert hasattr(dummy_window, "sc_zoom_reset")

    assert dummy_window.sc_new_chat.key().toString() == "Ctrl+N"
    assert dummy_window.sc_exit.key().toString() == "Ctrl+Q"
    assert dummy_window.sc_search.key().toString() == "Ctrl+F"


# =============================================================================
# 9. Интеграция с реальным MainWindow
# =============================================================================

def test_mainwindow_open_settings_menu_integration(qapp, monkeypatch):
    """Проверяет вызов _open_main_settings_menu на реальном экземпляре MainWindow."""
    from ui.main_window import MainWindow
    from ui.dialogs.settings_dialog import SettingsDialog

    mock_exec = MagicMock(return_value=1)
    monkeypatch.setattr(SettingsDialog, "exec", mock_exec)
    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    monkeypatch.setattr("ui.main_window.MainWindow._start_background_services", lambda self: None)
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])
    monkeypatch.setattr("services.process_manager.ProcessManager.start_lm_studio_async", lambda *a, **kw: None)

    win = MainWindow()
    win.show()

    try:
        win._open_main_settings_menu()
        mock_exec.assert_called_once()
    finally:
        win._shutdown_completed = True
        win.close()


# =============================================================================
# 10. UX и расширенные контракты (Шаг 3)
# =============================================================================

def test_settings_dialog_escape_key_closes(qapp, dummy_window):
    """Проверяет, что нажатие клавиши Escape закрывает диалог настроек."""
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent
    from ui.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog(dummy_window)
    dialog.show()

    # Эмулируем нажатие Escape
    esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dialog.keyPressEvent(esc_event)

    assert dialog.isVisible() is False
    dialog.deleteLater()


def test_dialog_controller_open_settings_dialog(qapp, dummy_window, monkeypatch):
    """Проверяет вызов _open_settings_dialog из DialogControllerMixin."""
    from ui.controllers.dialog_controller import DialogControllerMixin
    from ui.dialogs.settings_dialog import SettingsDialog

    class TestController(DialogControllerMixin, DummyMainWindow):
        pass

    controller = TestController()
    mock_exec = MagicMock(return_value=1)
    monkeypatch.setattr(SettingsDialog, "exec", mock_exec)

    # Открытие с начальной страницей appearance
    controller._open_settings_dialog(initial_route="appearance")
    mock_exec.assert_called_once()
    controller.deleteLater()


# =============================================================================
# 11. Проверка схлопывания навигации и элементов шапки
# =============================================================================

def test_navigation_collapsible_and_titlebar_controls(qapp, dummy_window):
    """Проверяет плавное схлопывание навигации, SettingCard для шрифта и кнопку [X]."""
    from ui.dialogs.settings_dialog import SettingsDialog
    from qfluentwidgets import SettingCard

    dialog = SettingsDialog(dummy_window)
    dialog.show()

    # 1. Заголовок окна: closeBtn видим, min/max скрыты
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # 2. Карточка шрифта является полноценным SettingCard
    assert hasattr(dialog, "btn_zoom_in")
    font_card = dialog.btn_zoom_in.parent()
    assert isinstance(font_card, SettingCard)

    # 3. Навигационная панель поддерживает складывание
    assert dialog.nav_panel.panel._isCollapsible is True
    dialog.deleteLater()


# =============================================================================
# 12. Проверка адаптивной палитры под цвет фона и кнопок (Вариант 1)
# =============================================================================

def test_adaptive_palette_extracted_from_wallpaper_and_updates_buttons(qapp, dummy_window):
    """
    Проверяет, что цвета фона, карточек и кнопок гармонично адаптируются под цвет
    активных обоев рабочего пространства (Material You / Fluent Tint).
    """
    from PyQt6.QtGui import QPixmap, QColor
    from ui.dialogs.settings_dialog import SettingsDialog, extract_adaptive_palette

    # 1. По умолчанию (без обоев) возвращается сбалансированная slate-палитра
    default_pal = extract_adaptive_palette(None)
    assert default_pal["bg"] == "#161B26"
    assert "btn" in default_pal
    assert "btn_hover" in default_pal

    # 2. Создаем обои с выраженным бирюзовым/аквамариновым оттенком (#00F0FF)
    cyan_pixmap = QPixmap(64, 64)
    cyan_pixmap.fill(QColor("#00F0FF"))
    cyan_pal = extract_adaptive_palette(cyan_pixmap)

    # Палитра должна подстроиться под оттенок обоев
    assert cyan_pal["bg"] != default_pal["bg"]
    assert cyan_pal["accent"].lower() == "#0ee9f5"
    assert "btn" in cyan_pal
    assert cyan_pal["btn_hover"] == cyan_pal["accent"]

    # 3. Проверяем динамическое применение палитры при смене обоев в окне
    dialog = SettingsDialog(dummy_window)
    dialog.show()

    # Исходная палитра диалога (по умолчанию)
    assert dialog._current_palette["bg"] == default_pal["bg"]
    assert default_pal["btn"] in dialog.styleSheet()

    # Устанавливаем в dummy_window новые обои и вызываем обновление
    dummy_window.background_pixmap = cyan_pixmap
    dialog.update_adaptive_palette()

    # Палитра диалога и стили кнопок должны перестроиться
    assert dialog._current_palette["accent"].lower() == "#0ee9f5"
    assert cyan_pal["btn"] in dialog.styleSheet()
    assert cyan_pal["btn_hover"] in dialog.styleSheet()

    dialog.deleteLater()


# =============================================================================
# 13. Проверка открытия консоли логов из активного модального окна настроек
# =============================================================================

def test_open_log_console_from_active_modal_dialog(qapp, monkeypatch):
    """
    Проверяет, что при открытии консоли логов из модального окна настроек
    создается модальный LogConsoleDialog с родителем SettingsDialog,
    предотвращая уход окна на задний план и блокировку взаимодействия.
    """
    from ui.controllers.dialog_controller import DialogControllerMixin
    from ui.dialogs.settings_dialog import SettingsDialog
    from ui.log_console import LogConsoleDialog

    class TestAppWindow(DialogControllerMixin, QMainWindow):
        def __init__(self):
            super().__init__()
            self._log_console_dialog = None

    win = TestAppWindow()
    dialog = SettingsDialog(win)

    mock_exec = MagicMock(return_value=0)
    monkeypatch.setattr(LogConsoleDialog, "exec", mock_exec)

    # Вызываем _open_log_console_dialog с родителем dialog (SettingsDialog)
    win._open_log_console_dialog(parent=dialog)

    # Должен быть вызван exec() для модального отображения поверх SettingsDialog
    mock_exec.assert_called_once()

    dialog.deleteLater()
    win.deleteLater()


