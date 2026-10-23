"""
Avatar Cropping Dialog for NeonRoute.
Provides interactive pan-and-zoom cropping for character avatars.
"""

from pathlib import Path
from typing import Optional, Union

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QPixmap, QImage, QColor, QPainterPath,
    QPen, QCursor, QWheelEvent, QMouseEvent
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QWidget, QMessageBox
)


class CropCanvas(QWidget):
    """
    Interactive canvas displaying the source image with mouse drag (pan)
    and mouse wheel (zoom), overlaid with a 300px transparent circular mask.
    """

    CIRCLE_DIAMETER = 300
    CIRCLE_RADIUS = 150

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.pixmap = pixmap
        self.scale_factor: float = 1.0
        self.offset: QPointF = QPointF(0, 0)
        self._is_dragging: bool = False
        self._last_mouse_pos: QPointF = QPointF(0, 0)

        self.setMinimumSize(420, 420)
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))

        # Initial scale so the image covers the 300px circle
        if not self.pixmap.isNull():
            w = max(1, self.pixmap.width())
            h = max(1, self.pixmap.height())
            min_dim = min(w, h)
            self.scale_factor = max(1.0, self.CIRCLE_DIAMETER / min_dim)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._last_mouse_pos = event.position()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging:
            delta = event.position() - self._last_mouse_pos
            self.offset += delta
            self._last_mouse_pos = event.position()
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self._is_dragging = False
            self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        zoom_speed = 1.08 if delta > 0 else 0.92
        new_scale = self.scale_factor * zoom_speed

        # Clamp zoom scale between reasonable limits
        if 0.1 <= new_scale <= 20.0:
            self.scale_factor = new_scale
            self.update()
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        cx = self.width() / 2.0
        cy = self.height() / 2.0

        # 1. Fill canvas background
        painter.fillRect(self.rect(), QColor(14, 19, 31))

        # 2. Draw transformed source pixmap
        if not self.pixmap.isNull():
            painter.save()
            painter.translate(cx + self.offset.x(), cy + self.offset.y())
            painter.scale(self.scale_factor, self.scale_factor)
            pw = self.pixmap.width()
            ph = self.pixmap.height()
            painter.drawPixmap(int(-pw / 2.0), int(-ph / 2.0), self.pixmap)
            painter.restore()

        # 3. Semi-transparent dark overlay: rgba(10, 15, 25, 0.75)
        overlay_color = QColor(10, 15, 25, int(255 * 0.75))
        rect_path = QPainterPath()
        rect_path.addRect(QRectF(0, 0, self.width(), self.height()))

        circle_rect = QRectF(
            cx - self.CIRCLE_RADIUS,
            cy - self.CIRCLE_RADIUS,
            self.CIRCLE_DIAMETER,
            self.CIRCLE_DIAMETER
        )
        circle_path = QPainterPath()
        circle_path.addEllipse(circle_rect)

        cutout_path = rect_path.subtracted(circle_path)
        painter.fillPath(cutout_path, overlay_color)

        # 4. Circular neon guide ring
        ring_pen = QPen(QColor(0, 240, 255, 200), 2)
        ring_pen.setCosmetic(True)
        painter.setPen(ring_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(circle_rect)

        painter.end()

    def get_cropped_image(self, target_size: int = 512) -> QImage:
        """
        Renders the visible content within the 300px circular mask
        into a square QImage of size target_size x target_size (512x512).
        """
        output_image = QImage(target_size, target_size, QImage.Format.Format_ARGB32)
        output_image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(output_image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        ratio = target_size / float(self.CIRCLE_DIAMETER)
        target_center = target_size / 2.0

        painter.translate(
            target_center + self.offset.x() * ratio,
            target_center + self.offset.y() * ratio
        )
        painter.scale(self.scale_factor * ratio, self.scale_factor * ratio)

        if not self.pixmap.isNull():
            pw = self.pixmap.width()
            ph = self.pixmap.height()
            painter.drawPixmap(int(-pw / 2.0), int(-ph / 2.0), self.pixmap)

        painter.end()
        return output_image


class AvatarCropDialog(QDialog):
    """
    QDialog for interactive avatar cropping:
    - Displays source image with drag and zoom wheel.
    - Dark overlay rgba(10, 15, 25, 0.75) with 300px central transparent circle.
    - Buttons: 'Отмена' and 'Применить кадрирование'.
    - On Apply: crops circle area into 512x512 square PNG and saves to destination_path.
    """

    def __init__(
        self,
        source_image_path: Union[str, Path],
        destination_path: Optional[Union[str, Path]] = None,
        parent=None
    ):
        super().__init__(parent)
        self.source_image_path = Path(source_image_path)
        self.destination_path = Path(destination_path) if destination_path else self._default_dest_path()

        self.setWindowTitle("Кадрирование аватара — NeonRoute")
        self.resize(500, 560)
        self.setMinimumSize(440, 500)
        self.setModal(True)

        self.pixmap = QPixmap(str(self.source_image_path))
        if self.pixmap.isNull():
            logger_pix_err = f"Не удалось загрузить изображение: {self.source_image_path}"
            # fallback empty pixmap
            self.pixmap = QPixmap(300, 300)
            self.pixmap.fill(QColor(30, 41, 59))

        self._setup_ui()

    def _default_dest_path(self) -> Path:
        return self.source_image_path.parent / f"{self.source_image_path.stem}_avatar.png"

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0E131F;
                color: #CBD5E1;
                border: 1px solid #1E273A;
                border-radius: 12px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 12px;
            }
            QLabel#HeaderTitle {
                color: #00F0FF;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #202D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#btn_apply {
                background-color: #004D54;
                border: 1px solid #00F0FF;
                color: #00F0FF;
                font-weight: bold;
            }
            QPushButton#btn_apply:hover {
                background-color: #00737D;
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header info
        header_title = QLabel("КАДРИРОВАНИЕ АВАТАРА", self)
        header_title.setObjectName("HeaderTitle")
        layout.addWidget(header_title)

        hint_lbl = QLabel("Перемещайте изображение мышью (drag) и масштабируйте колесиком (zoom)", self)
        hint_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        layout.addWidget(hint_lbl)

        # Interactive Canvas
        self.canvas = CropCanvas(self.pixmap, self)
        layout.addWidget(self.canvas, stretch=1)

        # Bottom Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_cancel = QPushButton("Отмена", self)
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        btn_layout.addStretch()

        self.btn_apply = QPushButton("Применить кадрирование", self)
        self.btn_apply.setObjectName("btn_apply")
        self.btn_apply.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_apply.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.btn_apply)

        layout.addLayout(btn_layout)

    def _on_apply(self):
        try:
            cropped = self.canvas.get_cropped_image(target_size=512)
            self.destination_path.parent.mkdir(parents=True, exist_ok=True)
            saved = cropped.save(str(self.destination_path), "PNG")
            if not saved:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить кадрированный аватар в:\n{self.destination_path}")
                return
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка при кадрировании: {e}")
