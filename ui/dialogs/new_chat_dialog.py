"""
New Chat Mode Selector Dialog for NeonRoute.
Glassmorphic popup dialog to choose chat mode: RP Chat, Assistant, or Engineer.
Modernized with QFluentWidgets components and Soft Card aesthetics under Agentrules.md.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QFrame, QLabel, QGraphicsDropShadowEffect
)
from qfluentwidgets import TransparentPushButton


class NewChatDialog(QDialog):
    """
    Интерактивный диалог выбора режима при создании нового чата.
    Оформлен в стиле Glassmorphism с матовыми акцентными карточками режимов.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_mode: str = ""
        self.setObjectName("NewChatDialog")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(290)
        self._setup_ui()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        container = QFrame(self)
        container.setObjectName("NewChatDialogContainer")
        container.setStyleSheet("""
            QFrame#NewChatDialogContainer {
                background-color: rgba(15, 20, 32, 0.96);
                border: 1px solid rgba(0, 240, 255, 0.28);
                border-radius: 14px;
            }
            QLabel#DialogHeader {
                color: #F8FAFC;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }
            QLabel#DialogSubHeader {
                color: #94A3B8;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
                background: transparent;
            }

            /* Base card button style */
            QPushButton.ModeCard {
                border-radius: 10px;
                text-align: left;
                outline: none;
                padding: 10px 14px;
            }

            /* RP Card */
            QPushButton#ModeCard_rp {
                background-color: rgba(26, 20, 42, 0.65);
                border: 1px solid rgba(167, 139, 250, 0.30);
            }
            QPushButton#ModeCard_rp:hover {
                background-color: rgba(167, 139, 250, 0.18);
                border: 1px solid #A78BFA;
            }
            QPushButton#ModeCard_rp:pressed {
                background-color: rgba(167, 139, 250, 0.28);
            }

            /* Assistant Card */
            QPushButton#ModeCard_assistant {
                background-color: rgba(15, 28, 42, 0.65);
                border: 1px solid rgba(0, 240, 255, 0.30);
            }
            QPushButton#ModeCard_assistant:hover {
                background-color: rgba(0, 240, 255, 0.18);
                border: 1px solid #00F0FF;
            }
            QPushButton#ModeCard_assistant:pressed {
                background-color: rgba(0, 240, 255, 0.28);
            }

            /* Engineer Card */
            QPushButton#ModeCard_engineer {
                background-color: rgba(38, 28, 18, 0.65);
                border: 1px solid rgba(245, 158, 11, 0.30);
            }
            QPushButton#ModeCard_engineer:hover {
                background-color: rgba(245, 158, 11, 0.18);
                border: 1px solid #F59E0B;
            }
            QPushButton#ModeCard_engineer:pressed {
                background-color: rgba(245, 158, 11, 0.28);
            }
        """)

        # Soft drop shadow for floating modal
        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 160))
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header Title
        lbl_title = QLabel("Новый диалог", container)
        lbl_title.setObjectName("DialogHeader")
        layout.addWidget(lbl_title)

        lbl_sub = QLabel("Выберите режим для начала сессии:", container)
        lbl_sub.setObjectName("DialogSubHeader")
        layout.addWidget(lbl_sub)

        layout.addSpacing(2)

        # 1. 🎭 RP Chat Card
        btn_rp = self._create_mode_card(
            container=container,
            object_name="ModeCard_rp",
            title="🎭  РП Чат",
            title_color="#C4B5FD",
            subtitle="Погружение в историю и отыгрыш ролей",
            on_click=lambda: self._select_mode("rp")
        )
        layout.addWidget(btn_rp)

        # 2. 💬 Assistant Card
        btn_assistant = self._create_mode_card(
            container=container,
            object_name="ModeCard_assistant",
            title="💬  Ассистент",
            title_color="#38BDF8",
            subtitle="Умный диалог, генерация и анализ",
            on_click=lambda: self._select_mode("assistant")
        )
        layout.addWidget(btn_assistant)

        # 3. 🛠️ Engineer Card
        btn_engineer = self._create_mode_card(
            container=container,
            object_name="ModeCard_engineer",
            title="🛠️  Инженер",
            title_color="#FBBF24",
            subtitle="Автономный код, запуск и разработка",
            on_click=lambda: self._select_mode("engineer")
        )
        layout.addWidget(btn_engineer)

        layout.addSpacing(4)

        # Close / Cancel button
        btn_cancel = TransparentPushButton("Отмена", container)
        btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(btn_cancel)

        root_layout.addWidget(container)

    def _create_mode_card(
        self,
        container: QFrame,
        object_name: str,
        title: str,
        title_color: str,
        subtitle: str,
        on_click
    ) -> QPushButton:
        btn = QPushButton(container)
        btn.setObjectName(object_name)
        btn.setProperty("class", "ModeCard")
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        card_layout = QVBoxLayout(btn)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(2)

        title_lbl = QLabel(title, btn)
        title_lbl.setStyleSheet(f"color: {title_color}; font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 13px; font-weight: 700; background: transparent;")
        title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card_layout.addWidget(title_lbl)

        sub_lbl = QLabel(subtitle, btn)
        sub_lbl.setStyleSheet("color: rgba(203, 213, 225, 0.70); font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 11px; background: transparent;")
        sub_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card_layout.addWidget(sub_lbl)

        btn.clicked.connect(on_click)
        return btn

    def _select_mode(self, mode: str):
        self.selected_mode = mode
        self.accept()
