"""
Interactive File Card Component for NeonRoute
Displays generated or attached documents (.xlsx, .docx, images)
with one-click launch ("Открыть файл") and folder location buttons.
"""

import os
import subprocess
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton
)


class FileCardWidget(QWidget):
    """
    Cyberpunk card representing an exported or attached document.
    """
    def __init__(self, file_path: Path | str, parent=None):
        super().__init__(parent)
        self.file_path = Path(file_path)
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("FileCardWidget")

        ext = self.file_path.suffix.lower()
        if ext in (".xlsx", ".xls"):
            icon_char = "📊"
            type_label = "EXCEL"
            badge_color = "#10B981"
            border_color = "#059669"
        elif ext in (".docx", ".doc"):
            icon_char = "📄"
            type_label = "WORD"
            badge_color = "#3B82F6"
            border_color = "#2563EB"
        elif ext in (".png", ".jpg", ".jpeg", ".webp"):
            icon_char = "🖼️"
            type_label = "IMAGE"
            badge_color = "#D946EF"
            border_color = "#C026D3"
        elif ext in (".wav", ".mp3"):
            icon_char = "🎵"
            type_label = "AUDIO"
            badge_color = "#00F0FF"
            border_color = "#0891B2"
        else:
            icon_char = "📄"
            type_label = "FILE"
            badge_color = "#94A3B8"
            border_color = "#475569"

        self.setStyleSheet(f"""
            #FileCardWidget {{
                background-color: #111723;
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 4px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)

        # Icon
        icon_label = QLabel(icon_char, self)
        icon_label.setStyleSheet("font-size: 22px;")
        layout.addWidget(icon_label)

        # File Details (Name, Size, Type badge)
        details_layout = QVBoxLayout()
        details_layout.setSpacing(2)

        title_label = QLabel(self.file_path.name, self)
        title_label.setStyleSheet("color: #F8FAFC; font-weight: bold; font-size: 13px;")
        details_layout.addWidget(title_label)

        # Size calculation
        size_str = "0 KB"
        if self.file_path.exists():
            bytes_size = self.file_path.stat().st_size
            if bytes_size > 1024 * 1024:
                size_str = f"{bytes_size / (1024 * 1024):.1f} MB"
            else:
                size_str = f"{bytes_size / 1024:.1f} KB"

        meta_label = QLabel(f"<span style='color:{badge_color}; font-weight:bold;'>[{type_label}]</span> {size_str}", self)
        meta_label.setStyleSheet("font-size: 11px; color: #94A3B8;")
        details_layout.addWidget(meta_label)

        layout.addLayout(details_layout, stretch=1)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        open_btn = QPushButton("Открыть файл", self)
        open_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #00F0FF;
                border: 1px solid #00F0FF;
                border-radius: 5px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #00F0FF;
                color: #050B14;
            }
        """)
        open_btn.clicked.connect(self._open_file)
        btn_layout.addWidget(open_btn)

        folder_btn = QPushButton("Папка", self)
        folder_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 5px;
                padding: 5px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                border-color: #64748B;
                color: #F8FAFC;
            }
        """)
        folder_btn.clicked.connect(self._open_in_folder)
        btn_layout.addWidget(folder_btn)

        layout.addLayout(btn_layout)

    def _open_file(self):
        """Launches the file in system default application."""
        if self.file_path.exists():
            try:
                os.startfile(str(self.file_path.resolve()))
            except Exception as e:
                print(f"Error opening file {self.file_path}: {e}")

    def _open_in_folder(self):
        """Highlights the file in Windows Explorer."""
        if self.file_path.exists():
            try:
                subprocess.Popen(f'explorer /select,"{str(self.file_path.resolve())}"')
            except Exception as e:
                print(f"Error highlighting file in explorer: {e}")
