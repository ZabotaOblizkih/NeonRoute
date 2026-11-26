"""
Menu Builder Component for NeonRoute.
Encapsulates settings popup menu creation and global shortcut bindings.
Modernized with QFluentWidgets RoundMenu and Action components.
"""

import os
from typing import TYPE_CHECKING
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QMenuBar, QMenu
from PyQt6.QtGui import QActionGroup, QKeySequence, QShortcut
from qfluentwidgets import (
    RoundMenu,
    Action,
    FluentIcon as FIF,
)
from ui.common.icon_utils import SafeFIF
from config import HISTORY_DIR

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ClickOnlyMenuBar(QMenuBar):
    """Легаси-заглушка для обратной совместимости."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)


def _create_round_menu(title: str, parent=None) -> RoundMenu:
    """Создает и стилизует RoundMenu в неоновой эстетике NeonRoute Glassmorphism."""
    menu = RoundMenu(title, parent=parent)
    menu.view.setObjectName("NeonRoundMenuView")
    menu.view.setStyleSheet("""
        MenuActionListWidget#NeonRoundMenuView {
            background-color: rgba(17, 24, 39, 0.96);
            border: 1px solid rgba(0, 240, 255, 0.22);
            border-radius: 10px;
            outline: none;
            font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
            font-size: 12px;
            padding: 4px;
        }
        MenuActionListWidget#NeonRoundMenuView::item {
            padding-left: 10px;
            padding-right: 14px;
            border-radius: 6px;
            margin: 1px 4px;
            border: none;
            color: #E2E8F0;
            min-height: 28px;
        }
        MenuActionListWidget#NeonRoundMenuView::item:disabled {
            color: rgba(148, 163, 184, 0.40);
        }
        MenuActionListWidget#NeonRoundMenuView::item:hover {
            background-color: rgba(0, 240, 255, 0.12);
            color: #00F0FF;
        }
        MenuActionListWidget#NeonRoundMenuView::item:selected {
            background-color: rgba(0, 240, 255, 0.16);
            color: #00F0FF;
        }
    """)
    return menu


class MenuBuilder:
    """
    Билдер всплывающего меню настроек (шестеренка) и глобальных клавиатурных сокращений.
    """

    @staticmethod
    def create_settings_menu(window: 'MainWindow') -> RoundMenu:
        """Создает всплывающее акриловое меню настроек для кнопки-шестеренки в сайдбаре."""
        root_menu = _create_round_menu("Параметры и настройки", window)

        # 1. Меню "⚙️ Система"
        system_menu = _create_round_menu("Система", root_menu)
        system_menu.setIcon(FIF.SETTING.icon())

        act_asst_settings = Action(FIF.ROBOT, "Настройки Эларии...", root_menu)
        act_asst_settings.triggered.connect(lambda *_: window._open_user_settings_dialog(0))
        system_menu.addAction(act_asst_settings)

        act_knowledge = Action(SafeFIF.BOOK_SHELF, "Знания Эларии...", root_menu)
        act_knowledge.setToolTip("Управление документами и ключевыми триггерами базы знаний")
        act_knowledge.triggered.connect(lambda *_: window._open_knowledge_manager_dialog())
        system_menu.addAction(act_knowledge)

        act_engineer_skills = Action(FIF.CODE, "Знания инженера...", root_menu)
        act_engineer_skills.setToolTip("Управление архитектурными скиллами, кодогенерацией и тест роутера")
        act_engineer_skills.triggered.connect(lambda *_: window._open_engineer_skills_dialog())
        system_menu.addAction(act_engineer_skills)

        act_gallery = Action(FIF.PEOPLE, "Галерея персонажей...", root_menu)
        act_gallery.triggered.connect(lambda *_: window._open_characters_gallery_dialog())
        system_menu.addAction(act_gallery)

        act_engineer_settings = Action(FIF.DEVELOPER_TOOLS, "Выбор моделей по режимам...", root_menu)
        act_engineer_settings.setToolTip("Настройка постоянных LLM моделей для Ассистента, РП и Инженера")
        act_engineer_settings.triggered.connect(lambda *_: window._open_mode_model_settings_dialog())
        system_menu.addAction(act_engineer_settings)

        act_llm_engine_settings = Action(FIF.SETTING, "Настройки движка LLM...", root_menu)
        act_llm_engine_settings.setToolTip("Параметры квантования KV-кэша и Flash Attention")
        act_llm_engine_settings.triggered.connect(lambda *_: window._open_llm_engine_settings_dialog())
        system_menu.addAction(act_llm_engine_settings)

        act_forge_settings = Action(FIF.PHOTO, "SD Forge...", root_menu)
        act_forge_settings.setToolTip("Путь к SD WebUI Forge, FP8-оптимизация и совместимость")
        act_forge_settings.triggered.connect(lambda *_: window._open_forge_settings_dialog())
        system_menu.addAction(act_forge_settings)

        system_menu.addSeparator()

        act_models_manager = Action(FIF.PALETTE, "Модели генерации...", root_menu)
        act_models_manager.setToolTip("Настройка чекпоинтов Forge, описаний и подсказок LLM")
        act_models_manager.triggered.connect(lambda *_: window._open_models_manager_dialog())
        system_menu.addAction(act_models_manager)

        system_menu.addSeparator()

        act_clean_cache = Action(FIF.DELETE, "Очистить кэш и временные файлы", root_menu)
        act_clean_cache.triggered.connect(lambda *_: window._open_clean_cache_dialog())
        system_menu.addAction(act_clean_cache)

        root_menu.addMenu(system_menu)

        # 2. Меню "🎨 Оформление"
        appearance_menu = _create_round_menu("Оформление", root_menu)
        appearance_menu.setIcon(FIF.BRUSH.icon())

        act_bg = Action(FIF.BRUSH, "Обои рабочего пространства...", root_menu)
        act_bg.triggered.connect(lambda *_: window._open_background_settings_dialog())
        appearance_menu.addAction(act_bg)

        act_dim = Action(FIF.VIEW, "Регулировка затемнения фона...", root_menu)
        act_dim.triggered.connect(lambda *_: window._open_background_settings_dialog())
        appearance_menu.addAction(act_dim)

        act_reset_theme = Action(FIF.SYNC, "Сбросить тему к стандартной", root_menu)
        act_reset_theme.triggered.connect(lambda *_: window._on_background_reset())
        appearance_menu.addAction(act_reset_theme)

        appearance_menu.addSeparator()

        zoom_menu = _create_round_menu("Масштаб шрифта", root_menu)
        zoom_menu.setIcon(FIF.ZOOM.icon() if hasattr(FIF, "ZOOM") else FIF.FONT.icon())
        act_zoom_in = Action(FIF.ZOOM_IN, "Крупнее (Ctrl +)", root_menu)
        act_zoom_in.triggered.connect(lambda *_: window._change_chat_font_size(1))
        act_zoom_out = Action(FIF.ZOOM_OUT, "Мельче (Ctrl -)", root_menu)
        act_zoom_out.triggered.connect(lambda *_: window._change_chat_font_size(-1))
        act_zoom_reset = Action(FIF.HISTORY, "Сбросить масштаб (13 pt)", root_menu)
        act_zoom_reset.triggered.connect(lambda *_: window._reset_chat_font_size())

        zoom_menu.addAction(act_zoom_in)
        zoom_menu.addAction(act_zoom_out)
        zoom_menu.addAction(act_zoom_reset)
        appearance_menu.addMenu(zoom_menu)

        appearance_menu.addSeparator()

        act_log_console = Action(FIF.COMMAND_PROMPT, "Консоль логов и отладки", root_menu)
        act_log_console.triggered.connect(lambda *_: window._open_log_console_dialog())
        appearance_menu.addAction(act_log_console)

        root_menu.addMenu(appearance_menu)

        # 3. Меню "💬 Диалоги"
        dialogs_menu = _create_round_menu("Диалоги", root_menu)
        dialogs_menu.setIcon(FIF.CHAT.icon())

        act_new_chat = Action(FIF.CHAT, "Новый диалог (Ctrl+N)", root_menu)
        act_new_chat.triggered.connect(lambda *_: window._on_new_chat_clicked())
        dialogs_menu.addAction(act_new_chat)

        act_sampling = Action(FIF.SETTING, "Настройка текстовой модели (по умолчанию)...", root_menu)
        act_sampling.triggered.connect(lambda *_: window._open_global_model_sampling_dialog())
        dialogs_menu.addAction(act_sampling)

        act_media_gallery = Action(FIF.PHOTO, "Медиа галерея", root_menu)
        act_media_gallery.triggered.connect(lambda *_: window._open_chat_media_gallery())
        dialogs_menu.addAction(act_media_gallery)

        def _open_history_dir(*_):
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(HISTORY_DIR))
            else:
                import subprocess
                subprocess.Popen(["explorer", str(HISTORY_DIR)])

        act_history_dir = Action(FIF.FOLDER, "Папка истории сессий", root_menu)
        act_history_dir.triggered.connect(_open_history_dir)
        dialogs_menu.addAction(act_history_dir)

        dialogs_menu.addSeparator()

        art_menu = _create_round_menu("Режим генерации иллюстраций", root_menu)
        art_menu.setIcon(FIF.PALETTE.icon())
        act_manual = Action("Вручную (кнопка в сообщении)", root_menu)
        act_manual.setCheckable(True)
        act_auto = Action("Автоматически (сразу через Forge)", root_menu)
        act_auto.setCheckable(True)

        art_group = QActionGroup(root_menu)
        art_group.addAction(act_manual)
        art_group.addAction(act_auto)

        is_auto = window.app_config.get("auto_generate_art", False)
        act_auto.setChecked(is_auto)
        act_manual.setChecked(not is_auto)

        def _set_art_mode(auto_val: bool):
            window.app_config["auto_generate_art"] = auto_val
            from utils.config_manager import ConfigManager
            ConfigManager.save_config(window.app_config)
            mode_str = "Автоматический" if auto_val else "По кнопке (Вручную)"
            window.statusBar().showMessage(f"Режим генерации иллюстраций: {mode_str}", 3000)

        act_manual.triggered.connect(lambda *_: _set_art_mode(False))
        act_auto.triggered.connect(lambda *_: _set_art_mode(True))

        art_menu.addAction(act_manual)
        art_menu.addAction(act_auto)
        dialogs_menu.addMenu(art_menu)

        dialogs_menu.addSeparator()

        act_export = Action(FIF.DOWNLOAD, "Экспорт текущей истории...", root_menu)
        act_export.triggered.connect(lambda *_: window._on_action_export_clicked())
        dialogs_menu.addAction(act_export)

        root_menu.addMenu(dialogs_menu)

        # 4. Меню "Справка"
        help_menu = _create_round_menu("Справка", root_menu)
        help_menu.setIcon(FIF.HELP.icon())

        act_check_deps = Action(FIF.ACCEPT, "Проверка компонентов...", root_menu)
        act_check_deps.setStatusTip("Проверить наличие Git и библиотек для режима Инженер")
        act_check_deps.triggered.connect(window.open_dependency_dialog)
        help_menu.addAction(act_check_deps)

        act_help = Action(FIF.HELP, "Горячие клавиши", root_menu)
        act_help.triggered.connect(lambda *_: window._open_help_dialog())
        help_menu.addAction(act_help)

        act_about = Action(FIF.INFO, "О программе NeonRoute", root_menu)
        act_about.triggered.connect(lambda *_: window._open_about_dialog())
        help_menu.addAction(act_about)

        act_updates = Action(FIF.SYNC, "Обновления...", root_menu)
        act_updates.triggered.connect(lambda *_: window._open_updates_dialog())
        help_menu.addAction(act_updates)

        act_bug_report = Action(FIF.FEEDBACK if hasattr(FIF, "FEEDBACK") else FIF.SEND, "Сообщить об ошибке...", root_menu)
        act_bug_report.triggered.connect(lambda *_: window.open_bug_report_dialog(source="menu"))
        help_menu.addAction(act_bug_report)

        root_menu.addMenu(help_menu)

        root_menu.addSeparator()

        # 5. Кнопка "Выход"
        act_exit = Action(FIF.POWER_BUTTON, "Выход (Ctrl+Q)", root_menu)
        act_exit.triggered.connect(lambda *_: window.close())
        root_menu.addAction(act_exit)

        return root_menu

    @staticmethod
    def setup_shortcuts(window: 'MainWindow') -> None:
        """Регистрирует глобальные горячие клавиши окна."""
        # Новый диалог (Ctrl+N)
        window.sc_new_chat = QShortcut(QKeySequence("Ctrl+N"), window)
        window.sc_new_chat.activated.connect(window._on_new_chat_clicked)

        # Выход (Ctrl+Q)
        window.sc_exit = QShortcut(QKeySequence("Ctrl+Q"), window)
        window.sc_exit.activated.connect(window.close)

        # Поиск по чату (Ctrl+F)
        window.sc_search = QShortcut(QKeySequence("Ctrl+F"), window)
        window.sc_search.activated.connect(window._show_search_bar)

        # Масштаб шрифта (Ctrl+, Ctrl-, Ctrl+0)
        window.sc_zoom_in_eq = QShortcut(QKeySequence("Ctrl+="), window)
        window.sc_zoom_in_eq.activated.connect(lambda: window._change_chat_font_size(1))

        window.sc_zoom_in_plus = QShortcut(QKeySequence("Ctrl++"), window)
        window.sc_zoom_in_plus.activated.connect(lambda: window._change_chat_font_size(1))

        window.sc_zoom_out = QShortcut(QKeySequence("Ctrl+-"), window)
        window.sc_zoom_out.activated.connect(lambda: window._change_chat_font_size(-1))

        window.sc_zoom_reset = QShortcut(QKeySequence("Ctrl+0"), window)
        window.sc_zoom_reset.activated.connect(window._reset_chat_font_size)

    @staticmethod
    def setup_menus(window: 'MainWindow') -> None:
        """Совместимость: инициализирует горячие клавиши."""
        MenuBuilder.setup_shortcuts(window)
