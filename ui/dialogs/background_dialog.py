"""
Background Settings Dialog for NeonRoute.
Allows users to choose custom wallpaper, adjust live darkening dim level (0% - 90%),
and reset background to default dark theme.
"""

from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QFileDialog, QFrame
)


class BackgroundSettingsDialog(QDialog):
    """
    Compact dialog for configuring MainWindow background image and dimming layer.
    """
    background_changed = pyqtSignal(str)   # path to new background image
    dimming_changed = pyqtSignal(int)      # alpha 0-255 (or percent 0-90%)
    background_reset = pyqtSignal()

    def __init__(self, current_path: Optional[str] = None, current_dim_alpha: int = 140, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка фона")
        self.resize(380, 260)
        self.setFixedSize(380, 260)

        self._current_path = current_path
        self._current_dim_alpha = current_dim_alpha

        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0E131F;
                color: #CBD5E1;
            }
            QLabel {
                color: #CBD5E1;
                font-size: 13px;
            }
            QPushButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #202D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton:pressed {
                background-color: #121A29;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #1C273C;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #00F0FF;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #00F0FF;
                width: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #00F0FF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # 1. Choose Image Button
        self.btn_change = QPushButton("🖼️ Сменить фон чата...")
        self.btn_change.clicked.connect(self._choose_image)
        layout.addWidget(self.btn_change)

        # Info label about current background
        self.lbl_info = QLabel(self._get_status_text())
        self.lbl_info.setStyleSheet("color: #64748B; font-size: 11px;")
        layout.addWidget(self.lbl_info)

        # 2. Dimming Slider
        slider_box = QVBoxLayout()
        slider_box.setSpacing(6)

        # Calculate percentage (0 - 90%)
        initial_pct = int(round((self._current_dim_alpha / 255.0) * 100))
        initial_pct = max(0, min(90, initial_pct))

        self.lbl_dim = QLabel(f"Затемнение фона: {initial_pct}%")
        self.lbl_dim.setStyleSheet("font-weight: 500;")
        slider_box.addWidget(self.lbl_dim)

        self.slider_dim = QSlider(Qt.Orientation.Horizontal)
        self.slider_dim.setRange(0, 90)
        self.slider_dim.setValue(initial_pct)
        self.slider_dim.valueChanged.connect(self._on_slider_changed)
        slider_box.addWidget(self.slider_dim)

        layout.addLayout(slider_box)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #1E283D;")
        layout.addWidget(divider)

        # 3. Action Buttons: Reset & Close
        btn_row = QHBoxLayout()

        self.btn_reset = QPushButton("🔄 Сбросить фон")
        self.btn_reset.setStyleSheet("""
            QPushButton {
                background-color: #1F1722;
                border-color: #3D223E;
                color: #F43F5E;
            }
            QPushButton:hover {
                background-color: #2D1A2F;
                border-color: #FF0055;
                color: #FF0055;
            }
        """)
        self.btn_reset.clicked.connect(self._reset_background)
        btn_row.addWidget(self.btn_reset)

        btn_row.addStretch()

        self.btn_close = QPushButton("Закрыть")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)

        layout.addLayout(btn_row)

    def _get_status_text(self) -> str:
        if self._current_path and Path(self._current_path).exists():
            return f"Активный файл: {Path(self._current_path).name}"
        return "Текущий фон: Стандартный темный"

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
            self.background_changed.emit(file_path)

    def _on_slider_changed(self, value: int):
        self.lbl_dim.setText(f"Затемнение фона: {value}%")
        alpha = int(round((value / 100.0) * 255))
        self._current_dim_alpha = alpha
        self.dimming_changed.emit(alpha)

    def _reset_background(self):
        self._current_path = None
        self.lbl_info.setText(self._get_status_text())
        self.background_reset.emit()
