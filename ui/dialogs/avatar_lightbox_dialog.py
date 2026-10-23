from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QColor
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QApplication


class AvatarLightboxDialog(QDialog):
    """
    Full-screen modal LightBox displaying the avatar in high resolution with dimmed background.
    Closes upon clicking anywhere or pressing Escape.
    """
    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self.image_path = Path(image_path)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background-color: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.img_lbl = QLabel(self)
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.img_lbl.setStyleSheet("""
            QLabel {
                border: 2px solid #00F0FF;
                border-radius: 14px;
                background-color: #0A0D14;
            }
        """)

        pix = QPixmap(str(self.image_path))
        if not pix.isNull():
            screen = QApplication.primaryScreen()
            scr_size = screen.size() if screen else (self.parent().size() if self.parent() else self.size())
            max_w = max(400, int(scr_size.width() * 0.82))
            max_h = max(400, int(scr_size.height() * 0.82))
            scaled = pix.scaled(
                max_w, max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.img_lbl.setPixmap(scaled)

        layout.addWidget(self.img_lbl)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(5, 11, 20, 220))
        super().paintEvent(event)

    def mousePressEvent(self, event):
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)
