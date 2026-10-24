import logging
from typing import Optional

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QPainter, QColor, QPixmap
from PyQt6.QtWidgets import QWidget, QMainWindow

logger = logging.getLogger("NeonRoute.WindowState")


class WindowStateMixin:
    """Миксин для отрисовки фона окна, переключения полноэкранного режима и обработки геометрии."""

    def _update_background_cache(self):
        if hasattr(self, 'background_pixmap') and self.background_pixmap and not self.background_pixmap.isNull():
            w, h = max(1, self.width()), max(1, self.height())
            buffer = QPixmap(w, h)
            buffer.fill(Qt.GlobalColor.transparent)

            p = QPainter(buffer)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            scaled = self.background_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)

            dim_alpha = getattr(self, 'background_dim_opacity', 140)
            p.fillRect(buffer.rect(), QColor(0, 0, 0, dim_alpha))
            p.end()

            self._bg_render_cache = buffer
        else:
            self._bg_render_cache = None

    def paintEvent(self, event):
        if hasattr(super(), "paintEvent"):
            try:
                super().paintEvent(event)
            except Exception:
                pass
        painter = QPainter(self)
        if getattr(self, '_bg_render_cache', None) and not self._bg_render_cache.isNull():
            painter.drawPixmap(0, 0, self._bg_render_cache)
        else:
            painter.fillRect(self.rect(), QColor(18, 18, 20))
        painter.end()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(True)
            if self.statusBar():
                self.statusBar().setVisible(True)
            if self.menuBar():
                self.menuBar().setVisible(True)
            if hasattr(self, "floating_close_btn"):
                self.floating_close_btn.setVisible(False)
            if hasattr(self, "btn_fullscreen"):
                self.btn_fullscreen.setText("⛶")
        else:
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            if self.statusBar():
                self.statusBar().setVisible(False)
            if self.menuBar():
                self.menuBar().setVisible(False)
            self.showFullScreen()
            if hasattr(self, "floating_close_btn"):
                self.floating_close_btn.setVisible(True)
                self._position_floating_close_btn()
            if hasattr(self, "btn_fullscreen"):
                self.btn_fullscreen.setText("🗗")

    def _position_floating_close_btn(self):
        if hasattr(self, "floating_close_btn"):
            self.floating_close_btn.move(self.width() - 48, 12)
            self.floating_close_btn.raise_()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            self._toggle_fullscreen()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self._toggle_fullscreen()
                event.accept()
                return
        if isinstance(self, QMainWindow):
            QMainWindow.keyPressEvent(self, event)

    def resizeEvent(self, event):
        if hasattr(super(), "resizeEvent"):
            try:
                super().resizeEvent(event)
            except Exception:
                pass
        self._update_background_cache()
        if self.isFullScreen() and hasattr(self, "floating_close_btn") and self.floating_close_btn.isVisible():
            self._position_floating_close_btn()

    def changeEvent(self, event):
        if hasattr(super(), "changeEvent"):
            try:
                super().changeEvent(event)
            except Exception:
                pass
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isFullScreen():
                if hasattr(self, "sidebar"):
                    self.sidebar.setVisible(False)
                if self.statusBar():
                    self.statusBar().setVisible(False)
                if self.menuBar():
                    self.menuBar().setVisible(False)
                if hasattr(self, "floating_close_btn"):
                    self.floating_close_btn.setVisible(True)
                    self._position_floating_close_btn()
                if hasattr(self, "btn_fullscreen"):
                    self.btn_fullscreen.setText("🗗")
                    self.btn_fullscreen.setToolTip("Выйти из полноэкранного режима (Esc / F11)")
            else:
                if hasattr(self, "sidebar"):
                    self.sidebar.setVisible(True)
                if self.statusBar():
                    self.statusBar().setVisible(True)
                if self.menuBar():
                    self.menuBar().setVisible(True)
                if hasattr(self, "floating_close_btn"):
                    self.floating_close_btn.setVisible(False)
                if hasattr(self, "btn_fullscreen"):
                    self.btn_fullscreen.setText("⛶")
                    self.btn_fullscreen.setToolTip("Полноэкранный режим чата (F11)")

    def nativeEvent(self, eventType, message):
        if hasattr(super(), "nativeEvent"):
            try:
                return super().nativeEvent(eventType, message)
            except Exception:
                pass
        return False, 0


__all__ = ["WindowStateMixin"]
