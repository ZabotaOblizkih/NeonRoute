"""
Backend Status Indicator Pill for NeonRoute
Displays live connection states for LM Studio, SD Forge, and F5-TTS.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel


class StatusPill(QWidget):
    """
    Compact status badge with glowing LED indicator.
    """
    def __init__(self, name: str, port: int, parent=None):
        super().__init__(parent)
        self.name = name
        self.port = port
        self.is_online = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        # LED Indicator Dot
        self.dot = QLabel("●", self)
        self.dot.setStyleSheet("color: #EF4444; font-size: 11px;")
        layout.addWidget(self.dot)

        # Name & Port label
        self.label = QLabel(f"{self.name} ({self.port})", self)
        self.label.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500;")
        layout.addWidget(self.label)

        self.setStyleSheet("""
            background-color: #111622;
            border: 1px solid #1E273A;
            border-radius: 12px;
        """)

    def set_status(self, online: bool, message: str = ""):
        self.is_online = online
        if online:
            self.dot.setStyleSheet("color: #00FF66; font-size: 11px;")  # Neon lime/emerald
            self.label.setStyleSheet("color: #E2E8F0; font-size: 11px; font-weight: 500;")
            self.setToolTip(f"{self.name} Online ({message or 'Ready'})")
        else:
            self.dot.setStyleSheet("color: #EF4444; font-size: 11px;")  # Neon red/coral
            self.label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
            self.setToolTip(f"{self.name} Offline: {message or 'Service unreachable'}")
