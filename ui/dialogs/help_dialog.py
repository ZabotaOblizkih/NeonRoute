"""
Help Dialog for NeonRoute (v0.9.1-beta).
Provides user guide, interactive shortcuts directory, VRAM management, operational modes,
workspace sandbox, art generation, reminders, and long-term memory.
Refactored to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Adaptive Palette).
"""

from typing import Optional, Dict, List
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QCursor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    PushButton,
    PrimaryPushButton,
    SearchLineEdit,
    CaptionLabel,
    SubtitleLabel,
    FluentIcon as FIF,
)

from config import APP_VERSION, WORKSPACE_DIR
from ui.common.palette_utils import extract_adaptive_palette


class HelpDialog(FramelessDialog):
    """
    Диалоговое окно «Горячие клавиши и руководство» NeonRoute в стиле Windows 11 Fluent Design.
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.window = parent
        self.setWindowTitle(f"Горячие клавиши и руководство — NeonRoute (v{APP_VERSION})")
        self.resize(760, 640)
        self.setMinimumSize(640, 500)

        # 1. Custom Fluent TitleBar с [X] в правом верхнем углу
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
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

        self.setObjectName("HelpDialog")
        self._current_palette: Dict[str, str] = {}
        self._cards: List[QFrame] = []
        self._card_data: List[tuple] = []

        self._setup_ui()
        self.update_adaptive_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои в иерархии родительских окон для адаптивной палитры."""
        p = self.parent()
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = p.parent()
        return None

    def update_adaptive_palette(self):
        """
        Адаптирует цветовую палитру окна, карточек и кнопок под активные обои.
        """
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            HelpDialog#HelpDialog {{
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
            QWidget#HelpBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #HelpDialog QWidget {{
                background-color: transparent;
                color: #CBD5E1;
            }}
            #HelpDialog QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QFrame.HelpCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
                padding: 14px 16px;
            }}
            QFrame.HelpCard:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QLabel.CardTitle {{
                color: {palette['group_title']};
                font-size: 15px;
                font-weight: 700;
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
            }}
            QLabel.CardBody {{
                color: #E2E8F0;
                font-size: 13px;
                line-height: 1.5;
            }}
            SearchLineEdit {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                color: #F8FAFC;
                padding-left: 8px;
            }}
            SearchLineEdit:focus {{
                border-color: {palette['accent']};
            }}
            PushButton, PrimaryPushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-weight: 500;
                font-size: 13px;
                padding: 6px 26px;
            }}
            PushButton:hover, PrimaryPushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PushButton:pressed, PrimaryPushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
            #HelpDialog QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            #HelpDialog QScrollBar::handle:vertical {{
                background: {palette['border']};
                min-height: 24px;
                border-radius: 4px;
            }}
            #HelpDialog QScrollBar::handle:vertical:hover {{
                background: {palette['accent']};
            }}
        """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        body_widget = QWidget(self)
        body_widget.setObjectName("HelpBodyWidget")
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(22, 14, 22, 18)
        body_layout.setSpacing(12)

        # 1. Верхняя панель: Заголовок и поиск
        top_layout = QHBoxLayout()
        top_layout.setSpacing(12)

        header_info = QVBoxLayout()
        header_info.setSpacing(2)
        title_lbl = QLabel(f"📖 Горячие клавиши и справочник (v{APP_VERSION})", body_widget)
        title_lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #F8FAFC;")
        header_info.addWidget(title_lbl)

        sub_lbl = CaptionLabel("Шорткаты управления, режимы, песочница и генерация изображений", body_widget)
        sub_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        header_info.addWidget(sub_lbl)
        top_layout.addLayout(header_info, stretch=1)

        # Быстрый поиск по справке
        self.search_box = SearchLineEdit(body_widget)
        self.search_box.setPlaceholderText("Поиск по шорткатам...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedWidth(240)
        self.search_box.textChanged.connect(self._on_search_changed)
        top_layout.addWidget(self.search_box)

        body_layout.addLayout(top_layout)

        # 2. Область прокрутки с карточками тем
        scroll = QScrollArea(body_widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 4, 10, 4)
        content_layout.setSpacing(12)

        # Регистрация карточек руководства
        self._card_data = [
            (
                "⌨️ Полный справочник горячих клавиш (Шорткаты)",
                "• Ctrl + N — создать новый диалог с выбором режима.\n"
                "• Ctrl + F — быстрый поиск по сообщениям диалога с навигацией (Enter / Shift+Enter).\n"
                "• Ctrl + B — открыть / скрыть правую панель Инспектора песочницы.\n"
                "• Ctrl + Q — безопасный выход из программы с выгрузкой моделей из VRAM.\n"
                "• F5 / Ctrl + Enter — запуск и верификация кода в песочнице.\n"
                "• Ctrl + S — сохранение текущего файла кода с авто-коммитом в Git.\n"
                "• F11 / Esc — полноэкранный режим / выход из полноэкранного режима или закрытие окон.\n"
                "• Ctrl + = / Ctrl + + — увеличение масштаба текста сообщений чата (+1 pt).\n"
                "• Ctrl + - — уменьшение масштаба текста сообщений чата (-1 pt).\n"
                "• Ctrl + 0 — сброс шрифта чата к эталонным 13 pt.\n"
                "• Ctrl + Колесо мыши — плавное интерактивное масштабирование текста."
            ),
            (
                "⚡ Режимы работы: «💬 Ассистент», «🎭 Ролевой чат» и «🛠️ Инженер»",
                "• «💬 Ассистент» (температура 0.70) — свободное общение, поиск идей, генерация текстов и арта, RAG-база знаний и микро-скиллы.\n"
                "• «🎭 Ролевой чат» — сюжетная сессия с компаньоном, выбор персоны протагониста, генерация сцен, лорбук и память мира.\n"
                "• «🛠️ Инженер» (температура 0.10) — строгий режим Mini-Antigravity. Модель фиксируется на Qwen, SD Forge мгновенно выгружает чекпоинт из памяти, освобождая до 3.2 ГБ VRAM под вычисления кода и Vision."
            ),
            (
                f"💻 Песочница {WORKSPACE_DIR} и инспектор кода",
                "• Создание и правка кода: файлы создаются (write_file) и обновляются (patch_file) строго внутри песочницы с принудительной синхронизацией fsync.\n"
                "• Запуск скриптов: кнопка «[ ▶ Запустить ]» на карточке задачи или F5 в инспекторе кода.\n"
                "• Безопасность (Path Guard): доступ к системным папкам (Windows, Program Files, .git, .env) надежно заблокирован."
            ),
            (
                "🎨 Генерация изображений и префикс !art",
                "• Прямой рендер !art: команды !art, !арт, !img полностью обходят текстовую LLM, мгновенно транслируют теги и направляют задачу напрямую в SD WebUI Forge.\n"
                "• Поддерживаемые CLI-флаги: --model, --ar (wide/tall/square), --size, --steps, --cfg, --denoise, --hires, --adetailer, --seed, --neg.\n"
                "• Защита VRAM: аппаратный VAE Tiling предотвращает скачки памяти при апскейле Hires.fix."
            ),
            (
                "🔔 Локальные напоминания от Эларии",
                "• Формат команд: «Напомни сделать бэкап через 15 минут», «Элария, напомни мне проверить логи через 45 сек».\n"
                "• Поведение: перехватывается локально без вызова LLM. По истечении таймера Элария пишет сообщение в чат, отображает неоновый тост над строкой статуса и отправляет нативное уведомление Windows Toast."
            ),
            (
                "💾 Долговременная память (CPU RAG)",
                "• Векторная память работает строго на CPU через ONNX Runtime (0 МБ VRAM), подтягивая 2-3 релевантных факта в системный блок <RelevantMemory>.\n"
                "• Команды управления: «Запомни: ...», «Что ты обо мне знаешь?», «Забудь [номер]», «Забудь всё».\n"
                "• В ролевом режиме память реального мира изолирована и заменяется на сюжетный лорбук <WorldState>."
            ),
            (
                "🖼️ Персонализация, фон и аватары",
                "• Профиль в режиме «Ассистент»: обращение к вам настраивается в меню «Система» -> «Настройки Эларии» (по умолчанию — «Пользователь»).\n"
                "• Обои чата: меню «Оформление» -> «Обои рабочего пространства...» позволяет выбрать изображение (PNG/JPG/WEBP) и отрегулировать затемнение.\n"
                "• Управление аватаром: клик ЛКМ по аватару в шапке открывает просмотр (LightBox), клик ПКМ открывает контекстное меню."
            ),
        ]

        self._cards.clear()
        for title, text in self._card_data:
            card = self._create_card(title, text)
            self._cards.append(card)
            content_layout.addWidget(card)

        scroll.setWidget(content_widget)
        body_layout.addWidget(scroll, stretch=1)

        # 3. Нижняя панель действий
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        self.btn_close = PushButton("Понятно", body_widget)
        self.btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)
        btn_layout.addStretch(1)

        body_layout.addLayout(btn_layout)

        root_layout.addWidget(self.titleBar)
        root_layout.addWidget(body_widget, 1)

    def _on_search_changed(self, text: str):
        """Фильтрует карточки по совпадению с поисковым запросом."""
        query = text.lower().strip()
        for card, (title, body) in zip(self._cards, self._card_data):
            if not query or query in title.lower() or query in body.lower():
                card.show()
            else:
                card.hide()

    def _create_card(self, title: str, text: str) -> QFrame:
        card = QFrame()
        card.setProperty("class", "HelpCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        title_lbl = QLabel(title, card)
        title_lbl.setProperty("class", "CardTitle")
        card_layout.addWidget(title_lbl)

        desc_lbl = QLabel(text, card)
        desc_lbl.setProperty("class", "CardBody")
        desc_lbl.setWordWrap(True)
        card_layout.addWidget(desc_lbl)

        return card

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "titleBar") and self.titleBar:
            if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
                self.titleBar.minBtn.hide()
            if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
                self.titleBar.maxBtn.hide()
            if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
                self.titleBar.closeBtn.setVisible(True)

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
