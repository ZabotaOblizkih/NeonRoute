"""
Settings Dialog for NeonRoute.
Modern PyQt-Fluent-Widgets (qfluentwidgets) multi-page settings window.
Refactored from legacy cascade popup menu into a unified Windows 11 Fluent Design window:
- Left-side collapsible NavigationInterface with smooth animation (48px <-> 210px)
- Top-right standard FluentTitleBar close button (min/max buttons hidden for dialog)
- Right-side QStackedWidget with SettingCardGroup and SettingCard components
- FontZoomCard inheriting from SettingCard for seamless visual consistency
- Dynamic Adaptive Palette (Material You / Fluent Tint): automatically adjusts window,
  card, and button colors to match the active wallpaper of MainWindow
- Complete CSS isolation preventing global QWidget {#0A0D14} color leaks
- Seamless delegation to MainWindow methods
"""

import os
from typing import TYPE_CHECKING, Optional, Dict, Any

from PyQt6.QtCore import Qt, QPoint, QSize
from PyQt6.QtGui import QFont, QCursor, QColor, QPixmap, QImage
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
    QLabel,
    QFrame,
    QSizePolicy,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    NavigationInterface,
    NavigationItemPosition,
    SingleDirectionScrollArea,
    SettingCardGroup,
    SettingCard,
    PushSettingCard,
    SwitchSettingCard,
    PushButton,
    PrimaryPushButton,
    TitleLabel,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    IconWidget,
)

from ui.common.icon_utils import SafeFIF
from ui.common.palette_utils import extract_adaptive_palette
from config import HISTORY_DIR, APP_VERSION

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class FontZoomCard(SettingCard):
    """
    Полноценная карточка настройки масштаба шрифта чата.
    Наследуется от SettingCard для идеального визуального единообразия с остальными карточками.
    """

    def __init__(self, parent=None):
        super().__init__(
            icon=FIF.ZOOM if hasattr(FIF, "ZOOM") else FIF.FONT,
            title="Масштаб шрифта чата",
            content="Быстрое масштабирование текста сообщений (Ctrl +, Ctrl -, Ctrl 0)",
            parent=parent,
        )
        self.btn_zoom_out = PushButton("Мельче (Ctrl -)", self)
        self.btn_zoom_out.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.btn_zoom_reset = PushButton("Сброс (13 pt)", self)
        self.btn_zoom_reset.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.btn_zoom_in = PushButton("Крупнее (Ctrl +)", self)
        self.btn_zoom_in.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.hBoxLayout.addWidget(self.btn_zoom_out)
        self.hBoxLayout.addSpacing(6)
        self.hBoxLayout.addWidget(self.btn_zoom_reset)
        self.hBoxLayout.addSpacing(6)
        self.hBoxLayout.addWidget(self.btn_zoom_in)
        self.hBoxLayout.addSpacing(16)


class SettingsDialog(FramelessDialog):
    """
    Современное окно параметров и настроек NeonRoute в стиле Fluent Design.
    """

    ROUTE_SYSTEM = "system"
    ROUTE_APPEARANCE = "appearance"
    ROUTE_DIALOGS = "dialogs"
    ROUTE_HELP = "help"

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.window = parent
        self.setWindowTitle("Параметры и настройки")
        self.resize(920, 640)
        self.setMinimumSize(780, 520)

        # 1. Custom Fluent TitleBar (кнопка закрытия [X] в правом верхнем углу)
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
        # Для модального диалога скрываем свертывание и развертывание, оставляем только [X] справа
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.close)

        # 2. Мягкая и гармоничная эстетика Fluent Soft Slate / Dark Mica с адаптивной палитрой
        self.setObjectName("SettingsDialog")
        self._current_palette: Dict[str, str] = {}
        self.update_adaptive_palette()

        self._setup_ui()

    def update_adaptive_palette(self):
        """
        Адаптирует цветовую палитру окна, карточек и кнопок под активные обои главного окна.
        """
        pixmap = getattr(self.window, "background_pixmap", None)
        if not pixmap and hasattr(self.window, "_bg_render_cache"):
            pixmap = getattr(self.window, "_bg_render_cache", None)

        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            SettingsDialog#SettingsDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#SettingsBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            NavigationInterface, NavigationPanel {{
                background-color: {palette['nav']};
                border: none;
                border-right: 1px solid {palette['border']};
                border-bottom-left-radius: 14px;
            }}
            #SettingsDialog QWidget {{
                background-color: transparent;
                color: #E2E8F0;
            }}
            #SettingsDialog QScrollArea,
            #SettingsDialog SingleDirectionScrollArea,
            #SettingsDialog QStackedWidget {{
                background-color: transparent;
                border: none;
            }}
            SettingCardGroup {{
                background-color: transparent;
            }}
            SettingCardGroup > QLabel {{
                color: {palette['group_title']};
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
                font-size: 14px;
                font-weight: 600;
                margin-top: 6px;
                margin-bottom: 6px;
            }}
            SettingCard, PushSettingCard, SwitchSettingCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            SettingCard:hover, PushSettingCard:hover, SwitchSettingCard:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            SettingCard QLabel#titleLabel, PushSettingCard QLabel#titleLabel, SwitchSettingCard QLabel#titleLabel {{
                color: {palette['text_primary']};
                font-weight: 600;
                font-size: 13px;
            }}
            SettingCard QLabel#contentLabel, PushSettingCard QLabel#contentLabel, SwitchSettingCard QLabel#contentLabel {{
                color: {palette['text_secondary']};
                font-size: 12px;
            }}
            SettingCard QPushButton, PushSettingCard QPushButton, PushButton, PrimaryPushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-weight: 500;
                font-size: 12px;
                padding: 4px 14px;
            }}
            SettingCard QPushButton:hover, PushSettingCard QPushButton:hover, PushButton:hover, PrimaryPushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            SettingCard QPushButton:pressed, PushSettingCard QPushButton:pressed, PushButton:pressed, PrimaryPushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
            NavigationToolButton {{
                background-color: transparent;
                border: none;
                border-radius: 5px;
            }}
            NavigationToolButton:hover {{
                background-color: {palette['card_hover']};
            }}
        """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Контейнер содержимого под шапкой FluentTitleBar
        body_widget = QWidget(self)
        body_widget.setObjectName("SettingsBodyWidget")
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # --- 1. Левая панель навигации (NavigationInterface со складной анимацией) ---
        self.nav_panel = NavigationInterface(
            body_widget,
            showMenuButton=True,
            showReturnButton=False,
            collapsible=True
        )
        self.nav_panel.setExpandWidth(210)
        self.nav_panel.setMinimumExpandWidth(500)
        self.nav_panel.panel.expand(useAni=False)
        body_layout.addWidget(self.nav_panel)

        # --- 2. Правая рабочая область (QStackedWidget со скроллами) ---
        self.stacked_widget = QStackedWidget(body_widget)
        body_layout.addWidget(self.stacked_widget, 1)

        root_layout.addWidget(self.titleBar)
        root_layout.addWidget(body_widget, 1)

        # Построение 4 разделов
        self._build_system_page()
        self._build_appearance_page()
        self._build_dialogs_page()
        self._build_help_page()

        # Регистрация элементов навигации
        self.nav_panel.addItem(
            routeKey=self.ROUTE_SYSTEM,
            icon=FIF.SETTING,
            text="Система",
            onClick=lambda: self.switch_to_page(0)
        )
        self.nav_panel.addItem(
            routeKey=self.ROUTE_APPEARANCE,
            icon=FIF.BRUSH,
            text="Оформление",
            onClick=lambda: self.switch_to_page(1)
        )
        self.nav_panel.addItem(
            routeKey=self.ROUTE_DIALOGS,
            icon=FIF.CHAT,
            text="Диалоги",
            onClick=lambda: self.switch_to_page(2)
        )
        self.nav_panel.addItem(
            routeKey=self.ROUTE_HELP,
            icon=FIF.HELP,
            text="Справка",
            onClick=lambda: self.switch_to_page(3)
        )

        # Выбираем первую категорию по умолчанию
        self.switch_to_page(0)

    def switch_to_page(self, index: int):
        """Переключает стек страниц и активный пункт навигации."""
        if 0 <= index < self.stacked_widget.count():
            self.stacked_widget.setCurrentIndex(index)
            keys = [self.ROUTE_SYSTEM, self.ROUTE_APPEARANCE, self.ROUTE_DIALOGS, self.ROUTE_HELP]
            if index < len(keys):
                self.nav_panel.setCurrentItem(keys[index])

    def showEvent(self, event):
        super().showEvent(event)
        # Гарантируем видимость кнопки закрытия в правом верхнем углу
        if hasattr(self, "titleBar") and self.titleBar:
            if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
                self.titleBar.minBtn.hide()
            if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
                self.titleBar.maxBtn.hide()
            if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
                self.titleBar.closeBtn.setVisible(True)

        # Актуализируем адаптивную палитру при показе
        self.update_adaptive_palette()

        if self.parent():
            try:
                parent_geom = self.parent().geometry()
                x = parent_geom.x() + (parent_geom.width() - self.width()) // 2
                y = parent_geom.y() + (parent_geom.height() - self.height()) // 2
                self.move(max(20, x), max(20, y))
            except Exception:
                pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    # =========================================================================
    # Страница 1: Система (Модели, ядро и базы знаний)
    # =========================================================================
    def _build_system_page(self):
        page = SingleDirectionScrollArea(self)
        page.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(16)

        hdr = SubtitleLabel("⚙️ Параметры системы и моделей", container)
        hdr.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 20px;")
        layout.addWidget(hdr)

        desc = CaptionLabel("Управление моделями инференса, профилями пользователей, базой знаний и скиллами", container)
        desc.setStyleSheet("color: #94A3B8; font-size: 13px; margin-bottom: 4px;")
        layout.addWidget(desc)

        # Группа 1: Нейросетевые модели и инференс
        group_models = SettingCardGroup("🧠 Нейросетевые модели и инференс", container)

        self.card_engineer_settings = PushSettingCard(
            text="Выбрать",
            icon=FIF.DEVELOPER_TOOLS,
            title="Выбор моделей по режимам...",
            content="Настройка постоянных LLM моделей для Ассистента, РП и Инженера",
            parent=group_models
        )
        self.card_engineer_settings.clicked.connect(
            lambda: self.window._open_mode_model_settings_dialog() if hasattr(self.window, "_open_mode_model_settings_dialog") else None
        )
        group_models.addSettingCard(self.card_engineer_settings)

        self.card_llm_engine = PushSettingCard(
            text="Настроить",
            icon=FIF.SETTING,
            title="Настройки движка LLM...",
            content="Параметры квантования KV-кэша (Q8/Q4/FP16) и Flash Attention",
            parent=group_models
        )
        self.card_llm_engine.clicked.connect(
            lambda: self.window._open_llm_engine_settings_dialog() if hasattr(self.window, "_open_llm_engine_settings_dialog") else None
        )
        group_models.addSettingCard(self.card_llm_engine)

        self.card_forge_settings = PushSettingCard(
            text="Настроить",
            icon=FIF.PHOTO,
            title="SD Forge...",
            content="Путь установки Forge, лаунчер, FP8-квантование SDXL и совместимость",
            parent=group_models
        )
        self.card_forge_settings.clicked.connect(
            lambda: self.window._open_forge_settings_dialog() if hasattr(self.window, "_open_forge_settings_dialog") else None
        )
        group_models.addSettingCard(self.card_forge_settings)

        self.card_models_manager = PushSettingCard(
            text="Управление",
            icon=FIF.PALETTE,
            title="Модели генерации...",
            content="Настройка чекпоинтов SD Forge, Civitai AutoFill и параметров пайплайна",
            parent=group_models
        )
        self.card_models_manager.clicked.connect(
            lambda: self.window._open_models_manager_dialog() if hasattr(self.window, "_open_models_manager_dialog") else None
        )
        group_models.addSettingCard(self.card_models_manager)

        layout.addWidget(group_models)

        # Группа 2: Базы знаний и персонализация
        group_knowledge = SettingCardGroup("📚 Базы знаний и персонализация", container)

        self.card_asst_settings = PushSettingCard(
            text="Настроить",
            icon=FIF.ROBOT,
            title="Настройки Эларии...",
            content="Параметры профиля пользователя, персоны и поведения ассистента",
            parent=group_knowledge
        )
        self.card_asst_settings.clicked.connect(
            lambda: self.window._open_user_settings_dialog(0) if hasattr(self.window, "_open_user_settings_dialog") else None
        )
        group_knowledge.addSettingCard(self.card_asst_settings)

        self.card_knowledge = PushSettingCard(
            text="Открыть",
            icon=SafeFIF.BOOK_SHELF,
            title="Знания Эларии...",
            content="Управление документами RAG, ключевыми триггерами и системным промптом",
            parent=group_knowledge
        )
        self.card_knowledge.clicked.connect(
            lambda: self.window._open_knowledge_manager_dialog() if hasattr(self.window, "_open_knowledge_manager_dialog") else None
        )
        group_knowledge.addSettingCard(self.card_knowledge)

        self.card_engineer_skills = PushSettingCard(
            text="Открыть",
            icon=FIF.CODE,
            title="Знания инженера...",
            content="Управление архитектурными скиллами (Skills Hub), TDD и тест-роутером",
            parent=group_knowledge
        )
        self.card_engineer_skills.clicked.connect(
            lambda: self.window._open_engineer_skills_dialog() if hasattr(self.window, "_open_engineer_skills_dialog") else None
        )
        group_knowledge.addSettingCard(self.card_engineer_skills)

        self.card_gallery = PushSettingCard(
            text="Открыть",
            icon=FIF.PEOPLE,
            title="Галерея персонажей...",
            content="Каталог карточек Tavern/Chub, создание сопартийцев и пошаговый перевод",
            parent=group_knowledge
        )
        self.card_gallery.clicked.connect(self._on_open_characters_gallery)
        group_knowledge.addSettingCard(self.card_gallery)

        self.card_clean_cache = PushSettingCard(
            text="Очистить",
            icon=FIF.DELETE,
            title="Очистить кэш и временные файлы",
            content="Фоновый подсчет и удаление кэша Forge, превью аватаров и компактизация памяти",
            parent=group_knowledge
        )
        self.card_clean_cache.clicked.connect(
            lambda: self.window._open_clean_cache_dialog() if hasattr(self.window, "_open_clean_cache_dialog") else None
        )
        group_knowledge.addSettingCard(self.card_clean_cache)

        layout.addWidget(group_knowledge)
        layout.addStretch(1)

        page.setWidget(container)
        self.stacked_widget.addWidget(page)

    # =========================================================================
    # Страница 2: Оформление (Визуальный стиль и масштабирование)
    # =========================================================================
    def _build_appearance_page(self):
        page = SingleDirectionScrollArea(self)
        page.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(16)

        hdr = SubtitleLabel("🎨 Внешний вид и оформление", container)
        hdr.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 20px;")
        layout.addWidget(hdr)

        desc = CaptionLabel("Настройка фонового изображения, затемнения обоев и масштабирования текста", container)
        desc.setStyleSheet("color: #94A3B8; font-size: 13px; margin-bottom: 4px;")
        layout.addWidget(desc)

        group = SettingCardGroup("Визуальный стиль и масштабирование", container)

        self.card_bg = PushSettingCard(
            text="Выбрать",
            icon=FIF.BRUSH,
            title="Обои рабочего пространства...",
            content="Выбор пользовательского фонового изображения главного окна (backgrounds/)",
            parent=group
        )

        def _open_background_dialog():
            if hasattr(self.window, "_open_background_settings_dialog"):
                self.window._open_background_settings_dialog()
            self.update_adaptive_palette()

        self.card_bg.clicked.connect(_open_background_dialog)
        group.addSettingCard(self.card_bg)

        self.card_dim = PushSettingCard(
            text="Настроить",
            icon=FIF.VIEW,
            title="Регулировка затемнения фона...",
            content="Уровень затемнения фоновых обоев от 0% до 90% для комфортного чтения",
            parent=group
        )

        def _open_dimming_dialog():
            if hasattr(self.window, "_open_background_settings_dialog"):
                self.window._open_background_settings_dialog()
            self.update_adaptive_palette()

        self.card_dim.clicked.connect(_open_dimming_dialog)
        group.addSettingCard(self.card_dim)

        self.card_reset_theme = PushSettingCard(
            text="Сбросить",
            icon=FIF.SYNC,
            title="Сбросить тему к стандартной",
            content="Сброс пользовательских обоев и возврат темы Windows 11 Mica/Acrylic",
            parent=group
        )

        def _reset_theme():
            if hasattr(self.window, "_on_background_reset"):
                self.window._on_background_reset()
            self.update_adaptive_palette()

        self.card_reset_theme.clicked.connect(_reset_theme)
        group.addSettingCard(self.card_reset_theme)

        # Карточка масштаба шрифта (на базе SettingCard для безупречного визуального стиля)
        font_card = FontZoomCard(group)
        self.btn_zoom_out = font_card.btn_zoom_out
        self.btn_zoom_reset = font_card.btn_zoom_reset
        self.btn_zoom_in = font_card.btn_zoom_in

        self.btn_zoom_out.clicked.connect(
            lambda: self.window._change_chat_font_size(-1) if hasattr(self.window, "_change_chat_font_size") else None
        )
        self.btn_zoom_reset.clicked.connect(
            lambda: self.window._reset_chat_font_size() if hasattr(self.window, "_reset_chat_font_size") else None
        )
        self.btn_zoom_in.clicked.connect(
            lambda: self.window._change_chat_font_size(1) if hasattr(self.window, "_change_chat_font_size") else None
        )

        group.addSettingCard(font_card)

        layout.addWidget(group)
        layout.addStretch(1)

        page.setWidget(container)
        self.stacked_widget.addWidget(page)

    # =========================================================================
    # Страница 3: Диалоги (Сессии, сэмплинг и история)
    # =========================================================================
    def _build_dialogs_page(self):
        page = SingleDirectionScrollArea(self)
        page.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(16)

        hdr = SubtitleLabel("💬 Диалоги и управление сессиями", container)
        hdr.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 20px;")
        layout.addWidget(hdr)

        desc = CaptionLabel("Управление сессиями, глобальными параметрами генерации и медиа архивами", container)
        desc.setStyleSheet("color: #94A3B8; font-size: 13px; margin-bottom: 4px;")
        layout.addWidget(desc)

        group = SettingCardGroup("Сессии и инференс", container)

        self.card_new_chat = PushSettingCard(
            text="Создать",
            icon=FIF.CHAT,
            title="Новый диалог (Ctrl+N)",
            content="Начать новую диалоговую сессию с выбором активного режима",
            parent=group
        )
        self.card_new_chat.clicked.connect(
            lambda: self.window._on_new_chat_clicked() if hasattr(self.window, "_on_new_chat_clicked") else None
        )
        group.addSettingCard(self.card_new_chat)

        self.card_sampling = PushSettingCard(
            text="Настроить",
            icon=FIF.SETTING,
            title="Настройка текстовой модели (по умолчанию)...",
            content="Глобальные параметры сэмплирования: температура, Top-P, Min-P, DRY, XTC",
            parent=group
        )
        self.card_sampling.clicked.connect(
            lambda: self.window._open_global_model_sampling_dialog(parent=self) if hasattr(self.window, "_open_global_model_sampling_dialog") else None
        )
        group.addSettingCard(self.card_sampling)

        self.card_media_gallery = PushSettingCard(
            text="Открыть",
            icon=FIF.PHOTO,
            title="Медиа галерея",
            content="Сводная сетка всех сгенерированных изображений из outputs/ и сессий",
            parent=group
        )
        self.card_media_gallery.clicked.connect(
            lambda: self.window._open_chat_media_gallery(parent=self) if hasattr(self.window, "_open_chat_media_gallery") else None
        )
        group.addSettingCard(self.card_media_gallery)

        def _open_history():
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(HISTORY_DIR))
            else:
                import subprocess
                subprocess.Popen(["explorer", str(HISTORY_DIR)])

        self.card_history_dir = PushSettingCard(
            text="Открыть",
            icon=FIF.FOLDER,
            title="Папка истории сессий",
            content="Открыть каталог history/ с файлами диалогов chat_<id>.json в Проводнике",
            parent=group
        )
        self.card_history_dir.clicked.connect(_open_history)
        group.addSettingCard(self.card_history_dir)

        self.switch_auto_art = SwitchSettingCard(
            icon=FIF.PALETTE,
            title="Автоматическая генерация иллюстраций",
            content="Автоматически отправлять арт-запросы в Forge сразу при получении промпта",
            parent=group
        )
        is_auto = False
        if hasattr(self.window, "app_config") and isinstance(self.window.app_config, dict):
            is_auto = self.window.app_config.get("auto_generate_art", False)
        self.switch_auto_art.setChecked(is_auto)

        def _on_auto_art_changed(checked: bool):
            if hasattr(self.window, "app_config") and isinstance(self.window.app_config, dict):
                self.window.app_config["auto_generate_art"] = checked
                try:
                    from utils.config_manager import ConfigManager
                    ConfigManager.save_config(self.window.app_config)
                except Exception:
                    pass
            if hasattr(self.window, "statusBar") and self.window.statusBar():
                mode_str = "Автоматический" if checked else "По кнопке (Вручную)"
                try:
                    self.window.statusBar().showMessage(f"Режим генерации иллюстраций: {mode_str}", 3000)
                except Exception:
                    pass

        self.switch_auto_art.checkedChanged.connect(_on_auto_art_changed)
        group.addSettingCard(self.switch_auto_art)

        self.card_export = PushSettingCard(
            text="Экспорт",
            icon=FIF.DOWNLOAD,
            title="Экспорт текущей истории...",
            content="Сохранить текущую ветку диалога в файл JSON в каталог exports/",
            parent=group
        )
        self.card_export.clicked.connect(
            lambda: self.window._on_action_export_clicked() if hasattr(self.window, "_on_action_export_clicked") else None
        )
        group.addSettingCard(self.card_export)

        layout.addWidget(group)
        layout.addStretch(1)

        page.setWidget(container)
        self.stacked_widget.addWidget(page)

    # =========================================================================
    # Страница 4: Справка (Диагностика и система)
    # =========================================================================
    def _build_help_page(self):
        page = SingleDirectionScrollArea(self)
        page.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(16)

        hdr = SubtitleLabel("❓ Справка и системная информация", container)
        hdr.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 20px;")
        layout.addWidget(hdr)

        desc = CaptionLabel("Аудит зависимостей окружения, справочник горячих клавиш и сведения о системе", container)
        desc.setStyleSheet("color: #94A3B8; font-size: 13px; margin-bottom: 4px;")
        layout.addWidget(desc)

        group = SettingCardGroup("Справка и поддержка", container)

        self.card_check_deps = PushSettingCard(
            text="Проверить",
            icon=FIF.ACCEPT,
            title="Проверка компонентов...",
            content="Диагностика Git, CUDA, PyTorch и пакетов режима Инженер (pygame, openpyxl)",
            parent=group
        )
        self.card_check_deps.clicked.connect(
            lambda: self.window.open_dependency_dialog() if hasattr(self.window, "open_dependency_dialog") else None
        )
        group.addSettingCard(self.card_check_deps)

        self.card_log_console = PushSettingCard(
            text="Открыть",
            icon=FIF.COMMAND_PROMPT,
            title="Консоль логов и отладки",
            content="Просмотр сообщений рантайма, диагностических логов и копирование трейсов",
            parent=group
        )
        self.card_log_console.clicked.connect(
            lambda: self.window._open_log_console_dialog(parent=self) if hasattr(self.window, "_open_log_console_dialog") else None
        )
        group.addSettingCard(self.card_log_console)

        self.card_help = PushSettingCard(
            text="Открыть",
            icon=FIF.HELP,
            title="Горячие клавиши",
            content="Интерактивная таблица всех шорткатов управления чатом и песочницей",
            parent=group
        )
        self.card_help.clicked.connect(
            lambda: self.window._open_help_dialog() if hasattr(self.window, "_open_help_dialog") else None
        )
        group.addSettingCard(self.card_help)

        self.card_about = PushSettingCard(
            text="О программе",
            icon=FIF.INFO,
            title="О программе NeonRoute",
            content=f"Сведения о версии v{APP_VERSION}, лицензии и аппаратном профиле 8 ГБ VRAM",
            parent=group
        )
        self.card_about.clicked.connect(
            lambda: self.window._open_about_dialog() if hasattr(self.window, "_open_about_dialog") else None
        )
        group.addSettingCard(self.card_about)

        self.card_updates = PushSettingCard(
            text="Проверить",
            icon=FIF.SYNC,
            title="Обновления",
            content="Проверка целостности файлов и выборочное обновление из GitHub Releases",
            parent=group
        )
        self.card_updates.clicked.connect(
            lambda: self.window._open_updates_dialog() if hasattr(self.window, "_open_updates_dialog") else None
        )
        group.addSettingCard(self.card_updates)

        self.card_bug_report = PushSettingCard(
            text="Сообщить",
            icon=FIF.FEEDBACK if hasattr(FIF, "FEEDBACK") else FIF.SEND,
            title="Сообщить об ошибке",
            content="Сформировать диагностический отчёт с предпросмотром и отправкой разработчику",
            parent=group
        )
        self.card_bug_report.clicked.connect(self._on_open_bug_report)
        group.addSettingCard(self.card_bug_report)

        self.card_exit = PushSettingCard(
            text="Выход",
            icon=FIF.POWER_BUTTON,
            title="Выход из программы",
            content="Грациозное завершение фоновых воркеров, выгрузка VRAM и выход (Ctrl+Q)",
            parent=group
        )
        self.card_exit.clicked.connect(self._on_exit_clicked)
        group.addSettingCard(self.card_exit)

        layout.addWidget(group)
        layout.addStretch(1)

        page.setWidget(container)
        self.stacked_widget.addWidget(page)

    def _on_open_characters_gallery(self):
        """Закрывает окно настроек перед открытием галереи, предотвращая наслоение DWM-слоев."""
        self._requested_open_gallery = True
        was_visible = self.isVisible()
        self.accept()
        # Для unit-тестов без модального event loop передаем синхронный вызов
        if not was_visible and hasattr(self.window, "_open_characters_gallery_dialog"):
            self.window._open_characters_gallery_dialog()

    def _on_open_bug_report(self):
        """Закрывает окно настроек перед открытием диалога отчёта об ошибке, избегая вложенной модальности."""
        self._requested_open_bug_report = True
        was_visible = self.isVisible()
        self.accept()
        if not was_visible and hasattr(self.window, "open_bug_report_dialog"):
            self.window.open_bug_report_dialog(source="settings")

    def _on_exit_clicked(self):
        """Безопасно закрывает диалог настроек и главное окно."""
        self.close()
        if hasattr(self.window, "close"):
            self.window.close()
