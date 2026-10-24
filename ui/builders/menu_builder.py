"""
Menu Builder Component for NeonRoute.
Encapsulates declarative top menubar setup and global shortcut bindings.
Modernized with QFluentWidgets RoundMenu and Action components under Agentrules.md.
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
from config import HISTORY_DIR

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ClickOnlyMenuBar(QMenuBar):
    """
    QMenuBar с открытием выпадающих списков строго по клику ЛКМ (clicked).
    Полностью подавляет открытие и переключение по наведению курсора (hover / mouseMoveEvent / enterEvent),
    обеспечивая стандартное поведение:
    - меню открывается строго по нажатию левой кнопки мыши (LMB);
    - пока меню открыто, клик по другому пункту закрывает предыдущее и открывает новое;
    - повторный клик по активному пункту или клик мимо закрывает меню.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(False)
        self._closing_action = None

    def addMenu(self, *args, **kwargs):
        action = super().addMenu(*args, **kwargs)
        menu = action.menu() if hasattr(action, "menu") else None
        if not menu and len(args) > 0 and isinstance(args[0], QMenu):
            menu = args[0]
        if menu:
            try:
                menu.aboutToHide.connect(lambda a=action: self._on_menu_about_to_hide(a))
            except Exception:
                pass
        return action

    def _on_menu_about_to_hide(self, action):
        self._closing_action = action
        QTimer.singleShot(180, self._clear_closing_action)

    def _clear_closing_action(self):
        self._closing_action = None

    def mousePressEvent(self, event):
        # Открытие меню строго по клику левой кнопки мыши
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return

        action = self.actionAt(event.pos())
        # Повторный клик по уже открытому или закрывающемуся пункту — закрывает меню без повторного открытия
        if action and (action == self.activeAction() or action == self._closing_action):
            self._closing_action = None
            self.setActiveAction(None)
            if action.menu():
                action.menu().close()
            event.accept()
            return

        self._closing_action = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Полностью подавляем открытие/переключение меню при наведении курсора (hover)
        event.ignore()


def _create_round_menu(title: str, parent=None) -> RoundMenu:
    """Creates and styles a RoundMenu in NeonRoute Glassmorphism aesthetics."""
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
    Билдер верхнего меню-бара и глобальных клавиатурных сокращений для MainWindow.
    Изолирует декларативное создание элементов интерфейса от контроллера окна.
    """

    @staticmethod
    def setup_menus(window: 'MainWindow') -> None:
        menubar = window.menuBar()
        menubar.setStyleSheet("""
            QMenuBar {
                background-color: rgba(15, 19, 28, 0.95);
                color: #CBD5E1;
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                padding: 2px 6px;
            }
            QMenuBar::item {
                background: transparent;
                padding: 5px 12px;
                border-radius: 6px;
                margin: 2px 2px;
            }
            QMenuBar::item:selected {
                background-color: rgba(0, 240, 255, 0.12);
                color: #00F0FF;
            }
            QMenuBar::item:pressed {
                background-color: rgba(0, 240, 255, 0.20);
                color: #00F0FF;
            }
        """)

        # А. Меню "💬 Диалоги"
        dialogs_menu = _create_round_menu("💬 Диалоги", window)

        act_new_chat = Action(FIF.CHAT, "Новый диалог", window)
        act_new_chat.setShortcut(QKeySequence("Ctrl+N"))
        act_new_chat.triggered.connect(lambda *_: window._on_new_chat_clicked())
        dialogs_menu.addAction(act_new_chat)

        act_sampling = Action(FIF.SETTING, "Настройка текстовой модели (по умолчанию)...", window)
        act_sampling.triggered.connect(lambda *_: window._open_global_model_sampling_dialog())
        dialogs_menu.addAction(act_sampling)

        act_media_gallery = Action(FIF.PHOTO, "Медиа галерея", window)
        act_media_gallery.triggered.connect(lambda *_: window._open_chat_media_gallery())
        dialogs_menu.addAction(act_media_gallery)

        def _open_history_dir(*_):
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(HISTORY_DIR))
            else:
                import subprocess
                subprocess.Popen(["explorer", str(HISTORY_DIR)])

        act_history_dir = Action(FIF.FOLDER, "Папка истории сессий", window)
        act_history_dir.triggered.connect(_open_history_dir)
        dialogs_menu.addAction(act_history_dir)

        dialogs_menu.addSeparator()

        art_menu = _create_round_menu("🎨 Режим генерации иллюстраций", window)
        act_manual = Action("Вручную (кнопка в сообщении)", window)
        act_manual.setCheckable(True)
        act_auto = Action("Автоматически (сразу через Forge)", window)
        act_auto.setCheckable(True)

        art_group = QActionGroup(window)
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

        act_export = Action(FIF.DOWNLOAD, "Экспорт текущей истории...", window)
        act_export.triggered.connect(lambda *_: window._on_action_export_clicked())
        dialogs_menu.addAction(act_export)

        dialogs_menu.addSeparator()

        act_exit = Action(FIF.POWER_BUTTON, "Выход", window)
        act_exit.setShortcut(QKeySequence("Ctrl+Q"))
        act_exit.triggered.connect(lambda *_: window.close())
        dialogs_menu.addAction(act_exit)

        menubar.addMenu(dialogs_menu)

        # Б. Меню "🎨 Оформление"
        appearance_menu = _create_round_menu("🎨 Оформление", window)

        act_bg = Action(FIF.BRUSH, "Обои рабочего пространства...", window)
        act_bg.triggered.connect(lambda *_: window._open_background_settings_dialog())
        appearance_menu.addAction(act_bg)

        act_dim = Action(FIF.VIEW, "Регулировка затемнения фона...", window)
        act_dim.triggered.connect(lambda *_: window._open_background_settings_dialog())
        appearance_menu.addAction(act_dim)

        act_reset_theme = Action(FIF.SYNC, "Сбросить тему к стандартной", window)
        act_reset_theme.triggered.connect(lambda *_: window._on_background_reset())
        appearance_menu.addAction(act_reset_theme)

        appearance_menu.addSeparator()

        zoom_menu = _create_round_menu("🔍 Масштаб шрифта", window)
        act_zoom_in = Action(FIF.ZOOM_IN, "Крупнее (Ctrl +)", window)
        act_zoom_in.triggered.connect(lambda *_: window._change_chat_font_size(1))
        act_zoom_out = Action(FIF.ZOOM_OUT, "Мельче (Ctrl -)", window)
        act_zoom_out.triggered.connect(lambda *_: window._change_chat_font_size(-1))
        act_zoom_reset = Action(FIF.HISTORY, "Сбросить масштаб (13 pt)", window)
        act_zoom_reset.triggered.connect(lambda *_: window._reset_chat_font_size())

        zoom_menu.addAction(act_zoom_in)
        zoom_menu.addAction(act_zoom_out)
        zoom_menu.addAction(act_zoom_reset)
        appearance_menu.addMenu(zoom_menu)

        appearance_menu.addSeparator()

        act_log_console = Action(FIF.COMMAND_PROMPT, "Консоль логов и отладки", window)
        act_log_console.triggered.connect(lambda *_: window._open_log_console_dialog())
        appearance_menu.addAction(act_log_console)

        menubar.addMenu(appearance_menu)

        # В. Меню "⚙️ Система"
        system_menu = _create_round_menu("⚙️ Система", window)

        act_asst_settings = Action(FIF.ROBOT, "Настройки Эларии...", window)
        act_asst_settings.triggered.connect(lambda *_: window._open_user_settings_dialog(0))
        system_menu.addAction(act_asst_settings)

        act_knowledge = Action(FIF.BOOK_SHELF, "Знания Эларии...", window)
        act_knowledge.setToolTip("Управление документами и ключевыми триггерами базы знаний")
        act_knowledge.triggered.connect(lambda *_: window._open_knowledge_manager_dialog())
        system_menu.addAction(act_knowledge)

        act_gallery = Action(FIF.PEOPLE, "Галерея персонажей...", window)
        act_gallery.triggered.connect(lambda *_: window._open_characters_gallery_dialog())
        system_menu.addAction(act_gallery)

        act_engineer_settings = Action(FIF.DEVELOPER_TOOLS, "Режим «Инженер»: выбор модели...", window)
        act_engineer_settings.triggered.connect(lambda *_: window._open_engineer_settings_dialog())
        system_menu.addAction(act_engineer_settings)

        system_menu.addSeparator()

        act_models_manager = Action(FIF.PALETTE, "Модели генерации...", window)
        act_models_manager.setToolTip("Настройка чекпоинтов Forge, описаний и подсказок LLM")
        act_models_manager.triggered.connect(lambda *_: window._open_models_manager_dialog())
        system_menu.addAction(act_models_manager)

        system_menu.addSeparator()

        act_clean_cache = Action(FIF.DELETE, "Очистить кэш и временные файлы", window)
        act_clean_cache.triggered.connect(lambda *_: window._open_clean_cache_dialog())
        system_menu.addAction(act_clean_cache)

        menubar.addMenu(system_menu)

        # Г. Меню "Справка"
        help_menu = _create_round_menu("Справка", window)

        act_check_deps = Action(FIF.ACCEPT, "Проверка компонентов...", window)
        act_check_deps.setStatusTip("Проверить наличие Git и библиотек для режима Инженер")
        act_check_deps.triggered.connect(window.open_dependency_dialog)
        help_menu.addAction(act_check_deps)

        act_help = Action(FIF.HELP, "Горячие клавиши", window)
        act_help.triggered.connect(lambda *_: window._open_help_dialog())
        help_menu.addAction(act_help)

        act_about = Action(FIF.INFO, "О программе NeonRoute", window)
        act_about.triggered.connect(lambda *_: window._open_about_dialog())
        help_menu.addAction(act_about)

        menubar.addMenu(help_menu)

    @staticmethod
    def setup_shortcuts(window: 'MainWindow') -> None:
        # In-chat Search (Ctrl+F)
        window.sc_search = QShortcut(QKeySequence("Ctrl+F"), window)
        window.sc_search.activated.connect(window._show_search_bar)

        # Font Zoom Shortcuts (11pt to 22pt)
        window.sc_zoom_in_eq = QShortcut(QKeySequence("Ctrl+="), window)
        window.sc_zoom_in_eq.activated.connect(lambda: window._change_chat_font_size(1))

        window.sc_zoom_in_plus = QShortcut(QKeySequence("Ctrl++"), window)
        window.sc_zoom_in_plus.activated.connect(lambda: window._change_chat_font_size(1))

        window.sc_zoom_out = QShortcut(QKeySequence("Ctrl+-"), window)
        window.sc_zoom_out.activated.connect(lambda: window._change_chat_font_size(-1))

        window.sc_zoom_reset = QShortcut(QKeySequence("Ctrl+0"), window)
        window.sc_zoom_reset.activated.connect(window._reset_chat_font_size)
