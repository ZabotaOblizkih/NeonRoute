"""
Help Dialog for NeonRoute (v0.9.0-beta).
Provides user guide and instructions regarding VRAM management, operational modes,
workspace sandbox, art generation, reminders, shortcuts, and long-term memory.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QWidget
)

from config import APP_VERSION


class HelpDialog(QDialog):
    """
    Dialog displaying clear usage instructions for NeonRoute features.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Инструкция — NeonRoute (v{APP_VERSION})")
        self.resize(680, 640)
        self.setMinimumSize(580, 520)
        self.setModal(True)
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0E131F;
                color: #CBD5E1;
                border: 1px solid #1E273A;
                border-radius: 12px;
            }
            QLabel {
                color: #CBD5E1;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QFrame.HelpCard {
                background-color: #121826;
                border: 1px solid #1E293B;
                border-radius: 10px;
                padding: 12px 16px;
            }
            QFrame.HelpCard:hover {
                border-color: rgba(0, 240, 255, 0.3);
            }
            QScrollBar:vertical {
                border: none;
                background-color: #0A0D14;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #202A3C;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #00F0FF;
            }
            QPushButton#CloseButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 8px;
                padding: 8px 26px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#CloseButton:hover {
                background-color: #202D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#CloseButton:pressed {
                background-color: #111827;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)

        # Header
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        title_lbl = QLabel(f"📖 Руководство пользователя NeonRoute (v{APP_VERSION})")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #00F0FF;")
        header_layout.addWidget(title_lbl)

        sub_lbl = QLabel("Полный справочник по горячим клавишам, режимам, песочнице и генерации")
        sub_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
        header_layout.addWidget(sub_lbl)

        layout.addLayout(header_layout)

        # Scroll Area for topic cards
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 4, 10, 4)
        content_layout.setSpacing(12)

        # 1. Краткая инструкция
        content_layout.addWidget(self._create_card(
            title="📖 Краткая инструкция",
            text="• Ctrl + N — создать новый диалог.\n"
                 "• Ctrl + F — поиск по сообщениям диалога с подсветкой и навигацией (Enter / Shift+Enter).\n"
                 "• F11 / Esc — вход и выход из полноэкранного режима (или закрытие модальных окон/поиска).\n"
                 "• Ctrl + = / Ctrl + + — увеличение масштаба шрифта чата (от 11 до 22 pt).\n"
                 "• Ctrl + - — уменьшение масштаба шрифта чата.\n"
                 "• Ctrl + 0 — сброс шрифта к эталонным 13 pt.\n"
                 "• Ctrl + Колесо мыши — интерактивное масштабирование текста."
        ))

        # 2. Режимы работы
        content_layout.addWidget(self._create_card(
            title="⚡ Режимы работы: «💬 Диалог» и «⚡ Инженер»",
            text="• «💬 Диалог» (температура 0.70) — свободное общение, поиск идей, генерация текстов и арта. Доступна регулировка сэмплинга через меню аватарки (Min-P, Repetition Penalty).\n"
                 "• «⚡ Инженер» (температура 0.10) — строгий режим Mini-Antigravity. Модель фиксируется на Qwen, SD Forge мгновенно выгружает чекпоинт из памяти, освобождая до 3.2 ГБ VRAM под вычисления кода и Vision."
        ))

        # 3. Песочница workspace
        content_layout.addWidget(self._create_card(
            title="💻 Песочница C:\\NeonRoute\\workspace и интерактивная консоль",
            text="• Создание и правка кода: файлы создаются (write_file) и обновляются (patch_file) строго внутри песочницы с принудительной синхронизацией fsync.\n"
                 "• Интерактивный запуск: кнопка «[ ▶ Запустить ]» на карточке файла открывает скрипт в отдельном окне командной строки Windows (cmd.exe /k), что обеспечивает стабильную работу скриптов с пользовательским вводом input().\n"
                 "• Безопасность (Path Guard): доступ к системным папкам (Windows, Program Files, .git, .env) заблокирован."
        ))

        # 4. Локальные напоминания
        content_layout.addWidget(self._create_card(
            title="🔔 Локальные напоминания от Эларии",
            text="• Формат команд: «Напомни сделать бэкап через 15 минут», «Элария, напомни мне проверить логи через 45 сек».\n"
                 "• Поведение: перехватывается локально без вызова LLM. По истечении таймера Элария пишет сообщение в активный чат, отображает неоновый тост над строкой статуса и отправляет нативное системное уведомление Windows Toast со звуком."
        ))

        # 5. Арт-директор и генерация
        content_layout.addWidget(self._create_card(
            title="🎨 Генерация изображений и префикс !",
            text="• Прямой рендер !art: команды !art, !арт, !img полностью обходят текстовую LLM, мгновенно транслируют теги и направляют задачу напрямую в SD WebUI Forge.\n"
                 "• «Золотая четверка» SD 1.5: Realistic Vision (фотореализм), CyberRealistic Classic (Sci-Fi, глянец), DreamShaper 8 (фэнтези, концепт-арт), MeinaHentai (2D аниме без искажений ADetailer).\n"
                 "• Защита VRAM: аппаратный VAE Tiling предотвращает скачки памяти при проходах Hires.fix."
        ))

        # 6. Долговременная память (CPU RAG)
        content_layout.addWidget(self._create_card(
            title="💾 Долговременная память (LanceDB + FastEmbed)",
            text="• Векторная память работает строго на CPU через ONNX Runtime (0 МБ VRAM), подтягивая 2-3 релевантных факта в системный блок <RelevantMemory>.\n"
                 "• Команды управления: «Запомни: ...», «Что ты обо мне знаешь?», «Забудь [номер]», «Забудь всё».\n"
                 "• В ролевом режиме память реального мира изолирована и заменяется на сюжетный лорбук <WorldState>."
        ))

        # 7. Персонализация и LightBox
        content_layout.addWidget(self._create_card(
            title="🖼️ Персонализация, фон и аватары",
            text="• Профиль в режиме «Ассистент»: обращение к вам настраивается в меню «Система» -> «Настройки Эларии» (по умолчанию — «Пользователь»).\n"
                 "• Персонажи в «Ролевом чате»: во вкладке «Ролевой чат» можно создать нескольких игровых персонажей (героев) с уникальными именами, описанием и аватарами, а также кнопкой «★ Сделать основным» выбрать активного героя по умолчанию.\n"
                 "• Фон чата: меню «Оформление» -> «🖼️ Обои рабочего пространства...» позволяет выбрать изображение (PNG/JPG/WEBP) и отрегулировать затемнение.\n"
                 "• Управление аватаром: клик ЛКМ по аватару в шапке открывает полноэкранный просмотр (Avatar LightBox), клик ПКМ открывает контекстное меню (смена аватара, поиск, медиагалерея, сброс)."
        ))

        scroll.setWidget(content_widget)
        layout.addWidget(scroll, stretch=1)

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("Понятно")
        btn_close.setObjectName("CloseButton")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

    def _create_card(self, title: str, text: str) -> QFrame:
        card = QFrame()
        card.setProperty("class", "HelpCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #38BDF8;")
        card_layout.addWidget(title_lbl)

        desc_lbl = QLabel(text)
        desc_lbl.setStyleSheet("font-size: 13px; color: #CBD5E1; line-height: 1.4;")
        desc_lbl.setWordWrap(True)
        card_layout.addWidget(desc_lbl)

        return card


