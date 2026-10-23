import os
from typing import TYPE_CHECKING
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from config import HISTORY_DIR

if TYPE_CHECKING:
    from ui.main_window import MainWindow


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
                background-color: #0F131C;
                color: #CBD5E1;
                border-bottom: 1px solid #1E273A;
            }
            QMenuBar::item {
                background: transparent;
                padding: 4px 10px;
            }
            QMenuBar::item:selected {
                background-color: #1A2234;
                color: #00F0FF;
            }
            QMenu {
                background-color: #121824;
                color: #E2E8F0;
                border: 1px solid #1E283D;
            }
            QMenu::item:selected {
                background-color: #1C263B;
                color: #00F0FF;
            }
        """)

        # А. Меню "💬 Диалоги"
        dialogs_menu = menubar.addMenu("💬 Диалоги")

        act_new_chat = QAction("📄 Новый диалог", window)
        act_new_chat.setShortcut(QKeySequence("Ctrl+N"))
        act_new_chat.triggered.connect(lambda *_: window._on_new_chat_clicked())
        dialogs_menu.addAction(act_new_chat)

        act_sampling = QAction("🎛️ Настройка текстовой модели (по умолчанию)...", window)
        act_sampling.triggered.connect(lambda *_: window._open_global_model_sampling_dialog())
        dialogs_menu.addAction(act_sampling)

        act_media_gallery = QAction("🖼️ Медиа галерея", window)
        act_media_gallery.triggered.connect(lambda *_: window._open_chat_media_gallery())
        dialogs_menu.addAction(act_media_gallery)

        def _open_history_dir(*_):
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(HISTORY_DIR))
            else:
                import subprocess
                subprocess.Popen(["explorer", str(HISTORY_DIR)])

        act_history_dir = QAction("📂 Папка истории сессий", window)
        act_history_dir.triggered.connect(_open_history_dir)
        dialogs_menu.addAction(act_history_dir)

        dialogs_menu.addSeparator()

        art_menu = dialogs_menu.addMenu("🎨 Режим генерации иллюстраций")
        act_manual = art_menu.addAction("Вручную (кнопка в сообщении)")
        act_manual.setCheckable(True)
        act_auto = art_menu.addAction("Автоматически (сразу через Forge)")
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

        dialogs_menu.addSeparator()

        act_export = QAction("📥 Экспорт текущей истории...", window)
        act_export.triggered.connect(lambda *_: window._on_action_export_clicked())
        dialogs_menu.addAction(act_export)

        dialogs_menu.addSeparator()

        act_exit = QAction("Выход", window)
        act_exit.setShortcut(QKeySequence("Ctrl+Q"))
        act_exit.triggered.connect(lambda *_: window.close())
        dialogs_menu.addAction(act_exit)

        # Б. Меню "🎨 Оформление"
        appearance_menu = menubar.addMenu("🎨 Оформление")

        act_bg = QAction("🖼️ Обои рабочего пространства...", window)
        act_bg.triggered.connect(lambda *_: window._open_background_settings_dialog())
        appearance_menu.addAction(act_bg)

        act_dim = QAction("🌓 Регулировка затемнения фона...", window)
        act_dim.triggered.connect(lambda *_: window._open_background_settings_dialog())
        appearance_menu.addAction(act_dim)

        act_reset_theme = QAction("🔄 Сбросить тему к стандартной", window)
        act_reset_theme.triggered.connect(lambda *_: window._on_background_reset())
        appearance_menu.addAction(act_reset_theme)

        appearance_menu.addSeparator()

        zoom_menu = appearance_menu.addMenu("🔍 Масштаб шрифта")
        act_zoom_in = zoom_menu.addAction("Крупнее (Ctrl +)")
        act_zoom_in.triggered.connect(lambda *_: window._change_chat_font_size(1))
        act_zoom_out = zoom_menu.addAction("Мельче (Ctrl -)")
        act_zoom_out.triggered.connect(lambda *_: window._change_chat_font_size(-1))
        act_zoom_reset = zoom_menu.addAction("Сбросить масштаб (13 pt)")
        act_zoom_reset.triggered.connect(lambda *_: window._reset_chat_font_size())

        appearance_menu.addSeparator()

        act_log_console = QAction("📟 Консоль логов и отладки", window)
        act_log_console.triggered.connect(lambda *_: window._open_log_console_dialog())
        appearance_menu.addAction(act_log_console)

        # В. Меню "⚙️ Система"
        system_menu = menubar.addMenu("⚙️ Система")

        act_asst_settings = QAction("⚡ Настройки Эларии...", window)
        act_asst_settings.triggered.connect(lambda *_: window._open_user_settings_dialog(0))
        system_menu.addAction(act_asst_settings)

        act_gallery = QAction("🎴 Галерея персонажей...", window)
        act_gallery.triggered.connect(lambda *_: window._open_characters_gallery_dialog())
        system_menu.addAction(act_gallery)

        act_engineer_settings = QAction("🛠️ Режим «Инженер»: выбор модели...", window)
        act_engineer_settings.triggered.connect(lambda *_: window._open_engineer_settings_dialog())
        system_menu.addAction(act_engineer_settings)

        system_menu.addSeparator()

        act_models_manager = QAction("🎨 Модели генерации...", window)
        act_models_manager.setToolTip("Настройка чекпоинтов Forge, описаний и подсказок LLM")
        act_models_manager.triggered.connect(lambda *_: window._open_models_manager_dialog())
        system_menu.addAction(act_models_manager)

        system_menu.addSeparator()

        act_clean_cache = QAction("🧹 Очистить кэш и временные файлы", window)
        act_clean_cache.triggered.connect(lambda *_: window._open_clean_cache_dialog())
        system_menu.addAction(act_clean_cache)

        # Г. Меню "Справка"
        help_menu = menubar.addMenu("Справка")

        act_check_deps = help_menu.addAction("Проверка компонентов...")
        act_check_deps.setStatusTip("Проверить наличие Git и библиотек для режима Инженер")
        act_check_deps.triggered.connect(window.open_dependency_dialog)

        act_help = QAction("⌨️ Горячие клавиши", window)
        act_help.triggered.connect(lambda *_: window._open_help_dialog())
        help_menu.addAction(act_help)

        act_about = QAction("ℹ️ О программе NeonRoute", window)
        act_about.triggered.connect(lambda *_: window._open_about_dialog())
        help_menu.addAction(act_about)

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
