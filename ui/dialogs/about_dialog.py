"""
About Dialog for NeonRoute.
Presents project information, version, developer, partner, and tech stack in cyberpunk neon styling.
Refactored to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Adaptive Palette).
"""

from typing import Optional, Dict
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QCursor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    PushButton,
    PrimaryPushButton,
)

from config import APP_NAME, APP_VERSION
from ui.common.palette_utils import extract_adaptive_palette


class AboutDialog(FramelessDialog):
    """
    Диалоговое окно «О программе» NeonRoute в стиле Windows 11 Fluent Design.
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.window = parent
        self.setWindowTitle(f"О программе — {APP_NAME}")
        self.resize(560, 500)
        self.setMinimumSize(480, 420)

        # 1. Custom Fluent TitleBar с [X] в правом верхнем углу
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.close)

        self.setObjectName("AboutDialog")
        self._current_palette: Dict[str, str] = {}
        self._setup_ui()
        self.update_adaptive_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои в иерархии родительских окон для адаптивной палитры."""
        p = self.parent()
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = p.parent()
        return None

    def update_adaptive_palette(self):
        """
        Адаптирует цветовую палитру окна, карточки и кнопок под активные обои.
        """
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            AboutDialog#AboutDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#AboutBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #AboutDialog QWidget {{
                background-color: transparent;
                color: #CBD5E1;
            }}
            QFrame#CardFrame {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 12px;
                padding: 16px;
            }}
            QFrame#BadgeFrame {{
                background-color: {palette['terminal_bg']};
                border: 1px solid {palette['terminal_border']};
                border-radius: 8px;
                padding: 8px 12px;
            }}
            QLabel#AppTitleLabel {{
                color: {palette['accent']};
                font-size: 22px;
                font-weight: 700;
                font-family: 'Segoe UI', -apple-system, sans-serif;
            }}
            QLabel#SubtitleLabel {{
                color: #94A3B8;
                font-size: 13px;
            }}
            QLabel#SectionHeader {{
                color: {palette['group_title']};
                font-weight: 600;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            QLabel#ValueLabel {{
                color: #F8FAFC;
                font-size: 13px;
            }}
            QLabel#CodeLabel {{
                color: #A7F3D0;
                font-family: 'Consolas', 'Cascadia Code', 'Courier New', monospace;
                font-size: 12px;
            }}
            QLabel#HwLabel {{
                color: #FCD34D;
                font-family: 'Consolas', 'Cascadia Code', 'Courier New', monospace;
                font-size: 12px;
            }}
            PushButton, PrimaryPushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-weight: 500;
                font-size: 13px;
                padding: 6px 24px;
            }}
            PushButton:hover, PrimaryPushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PushButton:pressed, PrimaryPushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
        """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        body_widget = QWidget(self)
        body_widget.setObjectName("AboutBodyWidget")
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(24, 16, 24, 20)
        body_layout.setSpacing(16)

        # Header Area
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_lbl = QLabel(f"⚡ {APP_NAME} v{APP_VERSION}", body_widget)
        self.title_lbl.setObjectName("AppTitleLabel")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.title_lbl)

        self.sub_lbl = QLabel("Локальный ИИ-клиент и персональный компаньон", body_widget)
        self.sub_lbl.setObjectName("SubtitleLabel")
        self.sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.sub_lbl)

        body_layout.addLayout(header_layout)

        # Main Info Card
        card = QFrame(body_widget)
        card.setObjectName("CardFrame")
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)
        card_layout.setContentsMargins(16, 16, 16, 16)

        # Developer
        dev_row = QHBoxLayout()
        dev_title = QLabel("Разработчик:", card)
        dev_title.setStyleSheet("font-weight: 600; color: #CBD5E1; min-width: 140px;")
        self.dev_val = QLabel("ZabotaOblizkih", card)
        self.dev_val.setStyleSheet("color: #38BDF8; font-weight: 600;")
        dev_row.addWidget(dev_title)
        dev_row.addWidget(self.dev_val, stretch=1)
        card_layout.addLayout(dev_row)

        # Partner
        partner_row = QHBoxLayout()
        partner_title = QLabel("Партнер разработки:", card)
        partner_title.setStyleSheet("font-weight: 600; color: #CBD5E1; min-width: 140px;")
        partner_val = QLabel("Разработано совместно с Gemini (Google AI)", card)
        partner_val.setStyleSheet("color: #F8FAFC;")
        partner_val.setWordWrap(True)
        partner_row.addWidget(partner_title)
        partner_row.addWidget(partner_val, stretch=1)
        card_layout.addLayout(partner_row)

        # Tech Stack Badge
        stack_badge = QFrame(card)
        stack_badge.setObjectName("BadgeFrame")
        stack_layout = QVBoxLayout(stack_badge)
        stack_layout.setSpacing(4)
        stack_layout.setContentsMargins(10, 8, 10, 8)

        stack_title = QLabel("Технологический стек:", stack_badge)
        stack_title.setObjectName("SectionHeader")
        stack_val = QLabel("PyQt6 • Python 3.10 • Qwen2.5-VL • SD WebUI Forge • F5-TTS", stack_badge)
        stack_val.setObjectName("CodeLabel")
        stack_val.setWordWrap(True)
        stack_layout.addWidget(stack_title)
        stack_layout.addWidget(stack_val)
        card_layout.addWidget(stack_badge)

        # Hardware Badge
        hw_badge = QFrame(card)
        hw_badge.setObjectName("BadgeFrame")
        hw_layout = QVBoxLayout(hw_badge)
        hw_layout.setSpacing(4)
        hw_layout.setContentsMargins(10, 8, 10, 8)

        hw_title = QLabel("Целевой аппаратный профиль:", hw_badge)
        hw_title.setObjectName("SectionHeader")
        hw_val = QLabel("NVIDIA GeForce RTX 5050 Laptop GPU (8 GB VRAM)", hw_badge)
        hw_val.setObjectName("HwLabel")
        hw_val.setWordWrap(True)
        hw_layout.addWidget(hw_title)
        hw_layout.addWidget(hw_val)
        card_layout.addWidget(hw_badge)

        body_layout.addWidget(card)
        body_layout.addStretch(1)

        # Bottom Button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        self.btn_close = PushButton("Закрыть", body_widget)
        self.btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)
        btn_layout.addStretch(1)

        body_layout.addLayout(btn_layout)

        root_layout.addWidget(self.titleBar)
        root_layout.addWidget(body_widget, 1)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "titleBar") and self.titleBar:
            if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
                self.titleBar.minBtn.hide()
            if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
                self.titleBar.maxBtn.hide()
            if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
                self.titleBar.closeBtn.setVisible(True)

        self.update_adaptive_palette()

        if self.parent():
            try:
                parent_geom = self.parent().geometry()
                x = parent_geom.x() + (parent_geom.width() - self.width()) // 2
                y = parent_geom.y() + (parent_geom.height() - self.height()) // 2
                self.move(max(20, x), max(20, y))
            except Exception:
                pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
        else:
            super().keyPressEvent(event)
