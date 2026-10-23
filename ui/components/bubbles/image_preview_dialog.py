"""
Full-resolution image preview modal dialog with system viewer integration.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QApplication
)


class ImagePreviewDialog(QDialog):
    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self.image_path = Path(image_path)
        
        pixmap = QPixmap(str(self.image_path))
        orig_w = pixmap.width() if not pixmap.isNull() else 0
        orig_h = pixmap.height() if not pixmap.isNull() else 0
        file_sz_kb = self.image_path.stat().st_size / 1024 if self.image_path.exists() else 0
        sz_str = f"{file_sz_kb / 1024:.2f} MB" if file_sz_kb > 1024 else f"{file_sz_kb:.0f} KB"

        self.setWindowTitle(f"NeonRoute Preview — {self.image_path.name} ({orig_w}x{orig_h} • {sz_str})")
        self.setStyleSheet("""
            QDialog {
                background-color: #0A0D14;
                border: 1px solid #00F0FF;
            }
        """)
        screen = QApplication.primaryScreen()
        if screen:
            max_w = int(screen.size().width() * 0.85)
            max_h = int(screen.size().height() * 0.85)
        else:
            max_w = 1600
            max_h = 1000

        self.setMinimumSize(400, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        hdr_layout = QHBoxLayout()
        info_lbl = QLabel(f"🖼️ <b>Оригинал:</b> {orig_w}x{orig_h} px | <b>Размер:</b> {sz_str} | 100% Full-res PNG", self)
        info_lbl.setStyleSheet("color: #38BDF8; font-size: 12px;")
        hdr_layout.addWidget(info_lbl)
        hdr_layout.addStretch()
        layout.addLayout(hdr_layout)

        img_label = QLabel(self)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                max_w, max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            img_label.setPixmap(scaled)
            self.resize(min(scaled.width() + 36, max_w), min(scaled.height() + 84, max_h))
        else:
            self.resize(850, 850)

        layout.addWidget(img_label, stretch=1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        open_sys_btn = QPushButton("📂 Открыть оригинал в Windows", self)
        open_sys_btn.setToolTip("Запустить исходный файл без потерь в штатном просмотрщике Windows")
        open_sys_btn.setStyleSheet("""
            QPushButton {
                background-color: #00F0FF;
                color: #050B14;
                font-weight: bold;
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #38F4FF;
            }
        """)
        open_sys_btn.clicked.connect(lambda: os.startfile(str(self.image_path)))

        folder_btn = QPushButton("📁 Показать в папке", self)
        folder_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #E2E8F0;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #00F0FF;
            }
        """)
        folder_btn.clicked.connect(lambda: subprocess.Popen(f'explorer /select,"{self.image_path}"'))

        close_btn = QPushButton("Закрыть", self)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                color: #F8FAFC;
            }
        """)
        close_btn.clicked.connect(self.accept)

        btn_layout.addWidget(open_sys_btn)
        btn_layout.addWidget(folder_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
