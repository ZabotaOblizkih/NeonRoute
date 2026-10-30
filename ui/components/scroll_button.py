"""
Floating Scroll Down Button for NeonRoute Chat.
Provides an aesthetic, minimalist Fluent / Dark Glass button with neon glow.
"""

from PyQt6.QtCore import Qt, QPointF, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import (
    QPainter, QColor, QPainterPath, QPen, QLinearGradient,
    QCursor, QPaintEvent, QEnterEvent
)
from PyQt6.QtWidgets import QPushButton, QGraphicsDropShadowEffect, QWidget


class FloatingScrollDownButton(QPushButton):
    """
    Floating circular button styled in Fluent / Dark Glass aesthetics.
    Drawn using QPainter with a delicate dark shadow in normal state and
    a diffused cyan/neon blue halo glow on hover.
    """

    def __init__(self, parent: QWidget = None, diameter: int = 38):
        super().__init__(parent)
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")

        # Shadow & Glow effect
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setColor(QColor(0, 0, 0, 140))
        self._shadow.setBlurRadius(10)
        self._shadow.setOffset(0, 2.5)
        self.setGraphicsEffect(self._shadow)

        # Hover animation progress: 0.0 (normal) to 1.0 (hovered)
        self._hover_progress = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._on_anim_value_changed)

    def _on_anim_value_changed(self, val):
        self._hover_progress = float(val)
        t = self._hover_progress

        # Blend shadow between deep dark depth and neon cyan glow
        r = int(0 * (1.0 - t) + 0 * t)
        g = int(0 * (1.0 - t) + 240 * t)
        b = int(0 * (1.0 - t) + 255 * t)
        a = int(140 * (1.0 - t) + 180 * t)
        blur = int(10 * (1.0 - t) + 18 * t)
        offset_y = 2.5 * (1.0 - t)

        self._shadow.setColor(QColor(r, g, b, a))
        self._shadow.setBlurRadius(blur)
        self._shadow.setOffset(0, offset_y)
        self.update()

    def enterEvent(self, event: QEnterEvent):
        super().enterEvent(event)
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        t = self._hover_progress
        w = float(self.width())
        h = float(self.height())
        radius = min(w, h) / 2.0 - 1.5
        center = QPointF(w / 2.0, h / 2.0)

        # 1. Dark Acrylic Glass Body (Matte semi-transparent acrylic)
        grad = QLinearGradient(center.x(), 1.5, center.x(), h - 1.5)
        bg_top = QColor(
            int(18 + 6 * t),
            int(24 + 10 * t),
            int(38 + 18 * t),
            int(220 + 20 * t)
        )
        bg_bot = QColor(
            int(10 + 4 * t),
            int(14 + 6 * t),
            int(24 + 10 * t),
            int(230 + 15 * t)
        )
        if self.isDown():
            bg_top = bg_top.darker(115)
            bg_bot = bg_bot.darker(115)

        grad.setColorAt(0.0, bg_top)
        grad.setColorAt(1.0, bg_bot)
        painter.setBrush(grad)

        # 2. Border with subtle cyan glow on hover
        border_r = int(56 * (1.0 - t) + 0 * t)
        border_g = int(189 * (1.0 - t) + 240 * t)
        border_b = int(248 * (1.0 - t) + 255 * t)
        border_a = int(45 * (1.0 - t) + 190 * t)
        border_pen = QPen(QColor(border_r, border_g, border_b, border_a), 1.2)
        painter.setPen(border_pen)
        painter.drawEllipse(center, radius, radius)

        # 3. Minimalist, thin, elegant Chevron Down
        chevron_path = QPainterPath()
        cx = center.x()
        cy = center.y() + (1.0 if self.isDown() else 0.0)

        chevron_path.moveTo(cx - 5.5, cy - 2.0)
        chevron_path.lineTo(cx, cy + 3.0)
        chevron_path.lineTo(cx + 5.5, cy - 2.0)

        chev_r = int(203 * (1.0 - t) + 0 * t)
        chev_g = int(213 * (1.0 - t) + 240 * t)
        chev_b = int(225 * (1.0 - t) + 255 * t)
        chev_a = int(220 * (1.0 - t) + 255 * t)

        icon_pen = QPen(QColor(chev_r, chev_g, chev_b, chev_a), 1.8)
        icon_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        icon_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        painter.setPen(icon_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(chevron_path)
