from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)


class ReminderToastWidget(QFrame):
    """Стилизованное всплывающее окно напоминания от Эларии."""
    def __init__(self, text: str, remind_at: str, parent=None):
        super().__init__(parent)
        self.setObjectName("ReminderToast")
        self.setWindowFlags(Qt.WindowType.SubWindow | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("""
            QFrame#ReminderToast {
                background: rgba(15, 23, 42, 0.95);
                border: 1px solid #00F0FF;
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        icon_lbl = QLabel("🔔", self)
        icon_lbl.setStyleSheet("font-size: 14px; background: transparent;")
        top_row.addWidget(icon_lbl)

        title_lbl = QLabel("Напоминание от Эларии", self)
        title_lbl.setStyleSheet("color: #00F0FF; font-weight: 700; font-size: 12px; background: transparent;")
        top_row.addWidget(title_lbl, 1)

        close_btn = QPushButton("✕", self)
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94A3B8;
                border: none;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #FFFFFF;
            }
        """)
        close_btn.clicked.connect(self.close)
        top_row.addWidget(close_btn)
        layout.addLayout(top_row)

        body_lbl = QLabel(text, self)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet("color: #E2E8F0; font-size: 13px; line-height: 1.3; background: transparent;")
        layout.addWidget(body_lbl)

        time_lbl = QLabel(remind_at, self)
        time_lbl.setStyleSheet("color: #64748B; font-size: 10px; background: transparent;")
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(time_lbl)

        QTimer.singleShot(14000, self.close)
