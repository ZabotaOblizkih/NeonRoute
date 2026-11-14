import logging
from typing import Optional

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QPainter, QColor, QPixmap
from PyQt6.QtWidgets import QWidget, QMainWindow

from ui.common.image_utils import create_hidpi_background_buffer

logger = logging.getLogger("NeonRoute.WindowState")


class WindowStateMixin:
    """Миксин для отрисовки фона окна, переключения полноэкранного режима и обработки геометрии."""

    def _update_background_cache(self):
        if hasattr(self, 'background_pixmap') and self.background_pixmap and not self.background_pixmap.isNull():
            dpr = self.devicePixelRatioF() if hasattr(self, 'devicePixelRatioF') else self.devicePixelRatio()
            dim_alpha = getattr(self, 'background_dim_opacity', 140)
            self._bg_render_cache = create_hidpi_background_buffer(
                self.background_pixmap,
                self.size(),
                dpr=dpr,
                dim_alpha=dim_alpha,
                bg_tint=QColor(0, 0, 0)
            )
        else:
            self._bg_render_cache = None

    def paintEvent(self, event):
        if hasattr(super(), "paintEvent"):
            try:
                super().paintEvent(event)
            except Exception:
                pass
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if getattr(self, '_bg_render_cache', None) and not self._bg_render_cache.isNull():
            painter.drawPixmap(0, 0, self._bg_render_cache)
        else:
            painter.fillRect(self.rect(), QColor(18, 18, 20))
        painter.end()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            was_maximized = getattr(self, "_was_maximized_before_fullscreen", False)
            if was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
                pre_geom = getattr(self, "_pre_fullscreen_geometry", None)
                if pre_geom and hasattr(pre_geom, "isValid") and pre_geom.isValid():
                    self.setGeometry(pre_geom)
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(True)
            if self.statusBar():
                self.statusBar().setVisible(True)
            if getattr(self, '_menu_bar', None):
                self._menu_bar.setVisible(True)
            if hasattr(self, "floating_close_btn"):
                self.floating_close_btn.setVisible(False)
            if hasattr(self, "btn_fullscreen"):
                self.btn_fullscreen.setText("⛶")
            if hasattr(self, "_sync_splitter_layout") and hasattr(self, "is_engineer_mode_active"):
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._sync_splitter_layout(self.is_engineer_mode_active()))
        else:
            self._was_maximized_before_fullscreen = self.isMaximized()
            if not self.isMaximized():
                self._pre_fullscreen_geometry = self.geometry()
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            if self.statusBar():
                self.statusBar().setVisible(False)
            if getattr(self, '_menu_bar', None):
                self._menu_bar.setVisible(False)
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
                if getattr(self, '_menu_bar', None):
                    self._menu_bar.setVisible(False)
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
                if getattr(self, '_menu_bar', None):
                    self._menu_bar.setVisible(True)
                if hasattr(self, "floating_close_btn"):
                    self.floating_close_btn.setVisible(False)
                if hasattr(self, "btn_fullscreen"):
                    self.btn_fullscreen.setText("⛶")
                    self.btn_fullscreen.setToolTip("Полноэкранный режим чата (F11)")
                if hasattr(self, "_sync_splitter_layout") and hasattr(self, "is_engineer_mode_active"):
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(0, lambda: self._sync_splitter_layout(self.is_engineer_mode_active()))

    def nativeEvent(self, eventType, message):
        if hasattr(super(), "nativeEvent"):
            try:
                return super().nativeEvent(eventType, message)
            except Exception:
                pass
        return False, 0


__all__ = ["WindowStateMixin"]
