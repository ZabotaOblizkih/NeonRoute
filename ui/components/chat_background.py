"""
Chat Background Viewport Component for NeonRoute
Provides high-performance wallpaper rendering with KeepAspectRatioByExpanding,
pre-scaled QPixmap caching in resizeEvent, adaptive dimming, and frosted glass support.
"""

from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPixmap, QCursor
from PyQt6.QtWidgets import QWidget, QMenu

from ui.common.image_utils import create_hidpi_background_buffer


class ChatBackgroundViewport(QWidget):
    change_background_requested = pyqtSignal()
    change_dimming_requested = pyqtSignal()
    reset_background_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self._bg_pixmap: Optional[QPixmap] = None
        self._scaled_pixmap: Optional[QPixmap] = None
        self._dim_alpha: int = 150  # Default ~60% darkening (0-255)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def set_background(self, pixmap: Optional[QPixmap], alpha: int = 150):
        """Sets the raw background pixmap and dimming alpha, updating cached scaled pixmap."""
        if pixmap is not None and pixmap.isNull():
            pixmap = None
        self._bg_pixmap = pixmap
        self._dim_alpha = max(20, min(240, alpha))
        self._update_scaled_pixmap()
        self.update()

    def set_dim_alpha(self, alpha: int):
        """Updates dimming alpha level without reloading pixmap."""
        self._dim_alpha = max(20, min(240, alpha))
        self._update_scaled_pixmap()
        self.update()

    def get_dim_alpha(self) -> int:
        return self._dim_alpha

    def has_background(self) -> bool:
        return self._bg_pixmap is not None and not self._bg_pixmap.isNull()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # CRITICAL OPTIMIZATION: Cache scaled pixmap strictly on resize, never in paintEvent
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self):
        if self._bg_pixmap and not self._bg_pixmap.isNull():
            sz = self.size()
            if sz.width() > 10 and sz.height() > 10:
                dpr = self.devicePixelRatioF() if hasattr(self, 'devicePixelRatioF') else self.devicePixelRatio()
                self._scaled_pixmap = create_hidpi_background_buffer(
                    self._bg_pixmap,
                    sz,
                    dpr=dpr,
                    dim_alpha=self._dim_alpha,
                    bg_tint=QColor(10, 12, 18)
                )
            else:
                self._scaled_pixmap = None
        else:
            self._scaled_pixmap = None

    def paintEvent(self, event):
        if not self._scaled_pixmap or self._scaled_pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawPixmap(0, 0, self._scaled_pixmap)
        painter.end()

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #121824;
                color: #E2E8F0;
                border: 1px solid #1E283D;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 18px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #1C263B;
                color: #00F0FF;
            }
            QMenu::separator {
                height: 1px;
                background-color: #1E283D;
                margin: 4px 6px;
            }
        """)

        act_change = menu.addAction("🖼️ Сменить фон чата...")
        act_change.triggered.connect(self.change_background_requested.emit)

        if self.has_background():
            act_dim = menu.addAction("🌓 Настроить затемнение фона...")
            act_dim.triggered.connect(self.change_dimming_requested.emit)

            act_reset = menu.addAction("🔄 Сбросить фон (дефолтная тема)")
            act_reset.triggered.connect(self.reset_background_requested.emit)

        menu.exec(QCursor.pos())
