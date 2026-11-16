"""
Background Settings Dialog for NeonRoute.
Allows users to choose custom wallpaper, adjust live darkening dim level (0% - 90%),
and reset background to default dark theme.
"""

from pathlib import Path
from typing import Optional, Dict
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QFileDialog, QFrame
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import FluentTitleBar

from ui.common.palette_utils import extract_adaptive_palette


class BackgroundSettingsDialog(FramelessDialog):
    """
    Compact dialog for configuring MainWindow background image and dimming layer.
    Styled with Windows 11 Fluent dark aesthetics, frameless custom title bar,
    and dynamic adaptive palette.
    """
    background_changed = pyqtSignal(str)   # path to new background image
    dimming_changed = pyqtSignal(int)      # alpha 0-255 (or percent 0-90%)
    background_reset = pyqtSignal()

    def __init__(self, current_path: Optional[str] = None, current_dim_alpha: int = 140, parent=None):
        super().__init__(parent=parent)
        self.setResizeEnabled(False)
        self.setObjectName("BackgroundSettingsDialog")
        self.setWindowTitle("Настройка фона")
        self.resize(380, 292)
        self.setFixedSize(380, 292)

        # Custom Fluent TitleBar (32px)
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(32)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            self.titleBar.closeBtn.setNormalColor(QColor("#E2E8F0"))
            self.titleBar.closeBtn.setHoverColor(QColor("#FFFFFF"))
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.reject)

        self._current_path = current_path
        self._current_dim_alpha = current_dim_alpha
        self._current_palette: Dict[str, str] = {}

        self._setup_ui()
        self.update_adaptive_palette()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Custom Frameless TitleBar at top
        root_layout.addWidget(self.titleBar)

        # 2. Main Body Container
        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("BackgroundBodyWidget")
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(20, 20, 20, 20)
        body_layout.setSpacing(16)

        # 1. Choose Image Button
        self.btn_change = QPushButton("🖼️ Сменить фон чата...", self.body_widget)
        self.btn_change.setObjectName("BtnChange")
        self.btn_change.clicked.connect(self._choose_image)
        body_layout.addWidget(self.btn_change)

        # Info label about current background
        self.lbl_info = QLabel(self._get_status_text(), self.body_widget)
        self.lbl_info.setObjectName("LblInfo")
        body_layout.addWidget(self.lbl_info)

        # 2. Dimming Slider
        slider_box = QVBoxLayout()
        slider_box.setSpacing(6)

        # Calculate percentage (0 - 90%)
        initial_pct = int(round((self._current_dim_alpha / 255.0) * 100))
        initial_pct = max(0, min(90, initial_pct))

        self.lbl_dim = QLabel(f"Затемнение фона: {initial_pct}%", self.body_widget)
        self.lbl_dim.setObjectName("LblDim")
        slider_box.addWidget(self.lbl_dim)

        self.slider_dim = QSlider(Qt.Orientation.Horizontal, self.body_widget)
        self.slider_dim.setObjectName("SliderDim")
        self.slider_dim.setRange(0, 90)
        self.slider_dim.setValue(initial_pct)
        self.slider_dim.valueChanged.connect(self._on_slider_changed)
        slider_box.addWidget(self.slider_dim)

        body_layout.addLayout(slider_box)

        # Divider
        divider = QFrame(self.body_widget)
        divider.setObjectName("DividerLine")
        divider.setFrameShape(QFrame.Shape.HLine)
        body_layout.addWidget(divider)

        # 3. Action Buttons: Reset & Close
        btn_row = QHBoxLayout()

        self.btn_reset = QPushButton("🔄 Сбросить фон", self.body_widget)
        self.btn_reset.setObjectName("BtnReset")
        self.btn_reset.clicked.connect(self._reset_background)
        btn_row.addWidget(self.btn_reset)

        btn_row.addStretch()

        self.btn_close = QPushButton("Закрыть", self.body_widget)
        self.btn_close.setObjectName("BtnClose")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)

        body_layout.addLayout(btn_row)

        root_layout.addWidget(self.body_widget, 1)

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои для адаптивной палитры (текущий путь, родитель, дефолтный фон)."""
        try:
            if self._current_path and Path(self._current_path).exists():
                pix = QPixmap(str(self._current_path))
                if not pix.isNull():
                    return pix
            p = self.parent()
            while p:
                if hasattr(p, "background_pixmap") and p.background_pixmap and not p.background_pixmap.isNull():
                    return p.background_pixmap
                if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap and not p.window.background_pixmap.isNull():
                    return p.window.background_pixmap
                if hasattr(p, "_bg_render_cache") and p._bg_render_cache and not p._bg_render_cache.isNull():
                    return p._bg_render_cache
                p = p.parent()
            from config import DEFAULT_BACKGROUND_PATH
            if DEFAULT_BACKGROUND_PATH.exists():
                pix = QPixmap(str(DEFAULT_BACKGROUND_PATH))
                if not pix.isNull():
                    return pix
        except Exception:
            pass
        return None

    def update_adaptive_palette(self):
        """
        Адаптирует цветовую палитру окна, рамок, меток, слайдера и кнопок под активные обои
        с безопасным fallback на стандартную темную палитру.
        """
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        if not palette:
            palette = extract_adaptive_palette(None)
        self._current_palette = palette

        bg = palette.get("bg", "#161B26")
        border = palette.get("border", "#2D3748")
        text_primary = palette.get("text_primary", "#F8FAFC")
        text_secondary = palette.get("text_secondary", "#94A3B8")
        btn_bg = palette.get("btn", "#2D3748")
        btn_border = palette.get("btn_border", "#475569")
        accent = palette.get("accent", "#6366F1")
        btn_hover_text = palette.get("btn_hover_text", "#FFFFFF")
        groove_bg = palette.get("card", "#21293B")

        # Применяем аккуратную рамку со скруглением 12px (без дублирования рамок на body_widget)
        self.setStyleSheet(f"""
            BackgroundSettingsDialog#BackgroundSettingsDialog {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            FluentTitleBar {{
                background-color: {bg};
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
                background: transparent;
            }}
            QWidget#BackgroundBodyWidget {{
                background-color: {bg};
                border: none;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
            }}
            QLabel {{
                background: transparent;
                color: {text_primary};
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
                font-size: 13px;
            }}
            QPushButton {{
                background-color: {btn_bg};
                color: {text_primary};
                border: 1px solid {btn_border};
                border-radius: 6px;
                padding: 7px 16px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {accent};
                border-color: {accent};
                color: {btn_hover_text};
            }}
            QPushButton:pressed {{
                background-color: {btn_bg};
                border-color: {accent};
            }}
            QPushButton#BtnReset {{
                background-color: rgba(239, 68, 68, 0.12);
                border: 1px solid rgba(239, 68, 68, 0.35);
                color: #F87171;
            }}
            QPushButton#BtnReset:hover {{
                background-color: #DC2626;
                border-color: #EF4444;
                color: #FFFFFF;
            }}
            QPushButton#BtnReset:pressed {{
                background-color: #991B1B;
                border-color: #DC2626;
            }}
            QPushButton#BtnClose {{
                background-color: {accent};
                border: 1px solid {accent};
                color: {btn_hover_text};
                font-weight: 600;
            }}
            QPushButton#BtnClose:hover {{
                background-color: {palette.get('group_title', accent)};
                border-color: {palette.get('group_title', accent)};
                color: #FFFFFF;
            }}
            QPushButton#BtnClose:pressed {{
                background-color: {btn_bg};
                border-color: {accent};
            }}
            QSlider::groove:horizontal {{
                height: 5px;
                background: {groove_bg};
                border: 1px solid {border};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {accent};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #E2E8F0;
                border: 1px solid {accent};
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {accent};
                border: 1px solid #FFFFFF;
            }}
            QFrame#DividerLine {{
                border: none;
                border-top: 1px solid {border};
                background: transparent;
                max-height: 1px;
            }}
        """)

        if hasattr(self, "titleBar") and self.titleBar and hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setNormalColor(QColor("#E2E8F0"))
            self.titleBar.closeBtn.setHoverColor(QColor("#FFFFFF"))

        self.lbl_info.setStyleSheet(f"background: transparent; color: {text_secondary}; font-size: 11px;")
        self.lbl_dim.setStyleSheet(f"background: transparent; color: {text_primary}; font-weight: 600;")

    def _get_status_text(self) -> str:
        if self._current_path and Path(self._current_path).exists():
            from config import DEFAULT_BACKGROUND_PATH, CANONICAL_BACKGROUND_NAME
            p_name = Path(self._current_path).name
            if p_name in ("default_background.jpg", CANONICAL_BACKGROUND_NAME):
                return "Текущий фон: Стандартный (NeonRoute)"
            return f"Активный файл: {p_name}"
        return "Текущий фон: Стандартный (NeonRoute)"

    def _choose_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите изображение фона",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*.*)"
        )
        if file_path:
            self._current_path = file_path
            self.lbl_info.setText(self._get_status_text())
            self.update_adaptive_palette()
            self.background_changed.emit(file_path)

    def _on_slider_changed(self, value: int):
        self.lbl_dim.setText(f"Затемнение фона: {value}%")
        alpha = int(round((value / 100.0) * 255))
        self._current_dim_alpha = alpha
        self.dimming_changed.emit(alpha)

    def _reset_background(self):
        self._current_path = None
        self.lbl_info.setText(self._get_status_text())
        self.update_adaptive_palette()
        self.background_reset.emit()

    def showEvent(self, event):
        super().showEvent(event)
        # Скрываем кнопки минимизации и максимизации строго после вызова super().showEvent(event)
        if hasattr(self, "titleBar") and self.titleBar:
            if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
                self.titleBar.minBtn.hide()
            if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
                self.titleBar.maxBtn.hide()
            if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
                self.titleBar.closeBtn.setVisible(True)
                self.titleBar.closeBtn.setNormalColor(QColor("#E2E8F0"))

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
            self.reject()
            event.accept()
        else:
            super().keyPressEvent(event)
