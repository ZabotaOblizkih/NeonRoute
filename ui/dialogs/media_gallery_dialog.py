"""
Chat Media Gallery Dialog for NeonRoute.
Displays responsive thumbnail grid of all images generated in the current conversation,
with full-size modal preview on click and quick access to Explorer folder.
"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QIcon, QCursor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QScrollArea, QWidget, QGridLayout, QFrame
)

from ui.components.chat_bubble import ImagePreviewDialog
from services.session_manager import SessionManager


class ThumbnailButton(QPushButton):
    """Clickable thumbnail image button with hover animation and glowing border."""
    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self.image_path = Path(image_path)
        self.setFixedSize(140, 140)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(f"{self.image_path.name}\n(Клик для полноразмерного просмотра)")

        # Render thumbnail
        pix = QPixmap(str(self.image_path))
        if not pix.isNull():
            thumb = pix.scaled(
                130, 130,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.setIcon(QIcon(thumb))
            self.setIconSize(QSize(130, 130))

        self.setStyleSheet("""
            QPushButton {
                background-color: #0E1422;
                border: 1px solid #1E2B42;
                border-radius: 8px;
                padding: 4px;
            }
            QPushButton:hover {
                border-color: #00F0FF;
                background-color: #162035;
            }
            QPushButton:pressed {
                border-color: #FF0055;
            }
        """)


class ChatMediaGalleryDialog(QDialog):
    """
    Dialog displaying all images generated or attached in the active chat session.
    """
    def __init__(
        self,
        session_id: str,
        session_images_dir: Optional[Path] = None,
        image_paths: Optional[List[Path]] = None,
        session_title: Optional[str] = None,
        parent=None
    ):
        super().__init__(parent)
        self.session_id = session_id
        self.session_title = session_title or "Диалог"
        if session_images_dir is not None:
            self.session_images_dir = Path(session_images_dir)
        elif session_id == "all_sessions":
            from config import OUTPUTS_DIR
            self.session_images_dir = OUTPUTS_DIR
        else:
            self.session_images_dir = SessionManager.get_session_images_dir(session_id)

        found_paths = []
        if self.session_images_dir.exists():
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                found_paths.extend(list(self.session_images_dir.glob(ext)))

        if image_paths:
            for p in image_paths:
                p_path = Path(p)
                if p_path.exists() and p_path not in found_paths:
                    found_paths.append(p_path)

        # Sort newest first
        found_paths.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
        self.image_paths = found_paths

        if self.session_id == "all_sessions":
            self.setWindowTitle(f"Медиа галерея — все изображения ({len(self.image_paths)})")
        else:
            title_suffix = f" • {self.session_title}" if self.session_title else ""
            self.setWindowTitle(f"Медиа галерея диалога — {len(self.image_paths)} изображений{title_suffix}")
        self.resize(760, 560)
        self.setMinimumSize(540, 400)

        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #090C12;
                color: #CBD5E1;
            }
            QScrollArea {
                background: transparent;
                border: 1px solid #1A2438;
                border-radius: 8px;
            }
            QPushButton {
                background-color: #151E2E;
                color: #E2E8F0;
                border: 1px solid #25354F;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #1F2D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton:pressed {
                background-color: #121A28;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header bar
        hdr_layout = QHBoxLayout()
        header_text = f"🖼️ Медиа галерея ({len(self.image_paths)})" if self.session_id == "all_sessions" else f"🖼️ Медиафайлы текущего диалога ({len(self.image_paths)})"
        title_lbl = QLabel(header_text)
        title_lbl.setStyleSheet("font-weight: bold; font-size: 14px; color: #00F0FF;")
        hdr_layout.addWidget(title_lbl)
        hdr_layout.addStretch()

        layout.addLayout(hdr_layout)

        # Grid of Thumbnails
        if not self.image_paths:
            empty_lbl = QLabel("В этом диалоге пока нет сгенерированных изображений.\nПопросите Ассистента создать арт или фото, и они появятся здесь!")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color: #64748B; font-size: 13px; margin: 40px;")
            layout.addWidget(empty_lbl, stretch=1)
        else:
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

            grid_container = QWidget()
            grid_container.setStyleSheet("background: transparent;")
            grid_layout = QGridLayout(grid_container)
            grid_layout.setContentsMargins(10, 10, 10, 10)
            grid_layout.setSpacing(12)

            columns = 4
            for idx, img_p in enumerate(self.image_paths):
                row = idx // columns
                col = idx % columns
                btn = ThumbnailButton(img_p, grid_container)
                btn.clicked.connect(lambda checked, p=img_p: self._preview_image(p))
                grid_layout.addWidget(btn, row, col)

            scroll.setWidget(grid_container)
            layout.addWidget(scroll, stretch=1)

        # Bottom Action Bar
        btn_layout = QHBoxLayout()

        btn_open_folder = QPushButton("📂 Открыть папку в Проводнике")
        btn_open_folder.clicked.connect(self._open_folder)
        btn_layout.addWidget(btn_open_folder)

        btn_layout.addStretch()

        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)

    def _preview_image(self, image_path: Path):
        dialog = ImagePreviewDialog(image_path, self)
        dialog.exec()

    def _open_folder(self):
        target_dir = self.session_images_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(target_dir))
            else:
                subprocess.Popen(["explorer", str(target_dir)])
        except Exception as e:
            pass
