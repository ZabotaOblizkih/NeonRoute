"""
New Chat Mode Selector Dialog for NeonRoute
Glassmorphic popup dialog to choose chat mode: RP Chat, Assistant, or Engineer.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QFrame


class NewChatDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_mode: str = ""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(230)
        self._setup_ui()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        container = QFrame(self)
        container.setObjectName("NewChatDialogContainer")
        container.setStyleSheet("""
            QFrame#NewChatDialogContainer {
                background-color: #101624;
                border: 1px solid rgba(0, 240, 255, 0.35);
                border-radius: 10px;
            }
            QPushButton {
                background-color: transparent;
                color: #E2E8F0;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 9px 14px;
                font-size: 13px;
                font-weight: 500;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #1A2338;
                border-color: rgba(0, 240, 255, 0.3);
                color: #00F0FF;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        btn_rp = QPushButton("🎭 РП Чат", container)
        btn_rp.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_rp.clicked.connect(lambda: self._select_mode("rp"))
        layout.addWidget(btn_rp)

        btn_assistant = QPushButton("💬 Ассистент", container)
        btn_assistant.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_assistant.clicked.connect(lambda: self._select_mode("assistant"))
        layout.addWidget(btn_assistant)

        btn_engineer = QPushButton("🛠️ Инженер", container)
        btn_engineer.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_engineer.clicked.connect(lambda: self._select_mode("engineer"))
        layout.addWidget(btn_engineer)

        root_layout.addWidget(container)

    def _select_mode(self, mode: str):
        self.selected_mode = mode
        self.accept()
