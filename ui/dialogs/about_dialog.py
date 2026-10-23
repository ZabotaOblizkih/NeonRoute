"""
About Dialog for NeonRoute.
Presents project information, version, developer, partner, and tech stack in cyberpunk neon styling.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame
)

from config import APP_NAME, APP_VERSION


class AboutDialog(QDialog):
    """
    Dialog displaying information about NeonRoute, version, developers, and tech stack.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе — NeonRoute")
        self.setFixedSize(520, 440)
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
                font-size: 13px;
            }
            QFrame#CardFrame {
                background-color: #121826;
                border: 1px solid #1E293B;
                border-radius: 10px;
                padding: 16px;
            }
            QPushButton#CloseButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 8px;
                padding: 8px 24px;
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
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        # Header area
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        title_lbl = QLabel(f"{APP_NAME} v{APP_VERSION}")
        title_lbl.setStyleSheet("font-size: 20px; font-weight: bold; color: #00F0FF;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(title_lbl)

        sub_lbl = QLabel("NeonRoute — локальный ИИ-клиент и персональный компаньон.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(sub_lbl)

        layout.addLayout(header_layout)

        # Card container with information
        card = QFrame()
        card.setObjectName("CardFrame")
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(14)

        # Developer
        dev_row = QHBoxLayout()
        dev_title = QLabel("Разработчик:")
        dev_title.setStyleSheet("font-weight: bold; color: #E2E8F0; min-width: 140px;")
        dev_val = QLabel("ZabotaOblizkih")
        dev_val.setStyleSheet("color: #38BDF8; font-weight: 600;")
        dev_row.addWidget(dev_title)
        dev_row.addWidget(dev_val, stretch=1)
        card_layout.addLayout(dev_row)

        # Development Partner
        partner_row = QHBoxLayout()
        partner_title = QLabel("Партнер разработки:")
        partner_title.setStyleSheet("font-weight: bold; color: #E2E8F0; min-width: 140px;")
        partner_val = QLabel("Разработано совместно с Gemini (Google AI)")
        partner_val.setStyleSheet("color: #E2E8F0;")
        partner_val.setWordWrap(True)
        partner_row.addWidget(partner_title)
        partner_row.addWidget(partner_val, stretch=1)
        card_layout.addLayout(partner_row)

        # Tech Stack
        stack_row = QVBoxLayout()
        stack_row.setSpacing(4)
        stack_title = QLabel("Стек:")
        stack_title.setStyleSheet("font-weight: bold; color: #E2E8F0;")
        stack_val = QLabel("PyQt6 • Python 3.10 • Qwen2.5-VL • SD WebUI Forge • F5-TTS")
        stack_val.setStyleSheet("color: #A7F3D0; font-family: 'Consolas', 'Courier New', monospace; font-size: 12px;")
        stack_val.setWordWrap(True)
        stack_row.addWidget(stack_title)
        stack_row.addWidget(stack_val)
        card_layout.addLayout(stack_row)

        # Target Hardware
        hw_row = QVBoxLayout()
        hw_row.setSpacing(4)
        hw_title = QLabel("Целевое железо:")
        hw_title.setStyleSheet("font-weight: bold; color: #E2E8F0;")
        hw_val = QLabel("NVIDIA GeForce RTX 5050 Laptop GPU (8 GB VRAM)")
        hw_val.setStyleSheet("color: #FCD34D; font-family: 'Consolas', 'Courier New', monospace; font-size: 12px;")
        hw_val.setWordWrap(True)
        hw_row.addWidget(hw_title)
        hw_row.addWidget(hw_val)
        card_layout.addLayout(hw_row)

        layout.addWidget(card)

        # Bottom row with close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.setObjectName("CloseButton")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)
