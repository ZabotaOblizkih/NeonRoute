from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFrame, QLabel, QProgressBar, QApplication
)


class ShutdownProgressDialog(QDialog):
    """
    Compact frameless modal dialog showing shutdown progress and VRAM deallocation
    with dark glassmorphic styling and smooth indeterminate animation.
    """
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(380, 160)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        container = QFrame(self)
        container.setObjectName("ShutdownCard")
        container.setStyleSheet("""
            #ShutdownCard {
                background: rgba(20, 22, 30, 0.95);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 12px;
                padding: 20px;
            }
        """)

        card_layout = QVBoxLayout(container)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(14)

        # Title
        title_lbl = QLabel("Завершение работы...", container)
        title_lbl.setStyleSheet("""
            color: #FFFFFF;
            font-size: 15px;
            font-weight: 700;
            background: transparent;
        """)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title_lbl)

        # Animated indeterminate progress bar
        self.progress_bar = QProgressBar(container)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(10, 12, 18, 0.8);
                border: 1px solid rgba(0, 240, 255, 0.3);
                border-radius: 3px;
                height: 6px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00F0FF, stop:1 #7000FF);
                border-radius: 3px;
            }
        """)
        card_layout.addWidget(self.progress_bar)

        # Status text
        status_lbl = QLabel("Освобождение VRAM и сохранение состояния...", container)
        status_lbl.setStyleSheet("""
            color: #94A3B8;
            font-size: 12px;
            font-weight: 500;
            background: transparent;
        """)
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(status_lbl)

        layout.addWidget(container)

        primary_screen = QApplication.primaryScreen()
        if primary_screen:
            geo = primary_screen.geometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)
