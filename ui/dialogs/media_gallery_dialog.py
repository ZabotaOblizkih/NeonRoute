"""
Chat Media Gallery Dialog for NeonRoute.
Displays responsive thumbnail grid of all images generated in the current conversation
or across all sessions, with full-size modal preview on click and quick access to Explorer folder.
Refactored to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Dynamic Adaptive Palette).
"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Dict
from PyQt6.QtCore import Qt, QSize, QPoint
from PyQt6.QtGui import QPixmap, QIcon, QCursor, QColor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QScrollArea,
    QGridLayout,
    QFrame,
    QApplication,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    PushButton,
    PrimaryPushButton,
    SearchLineEdit,
    SubtitleLabel,
    CaptionLabel,
    FluentIcon as FIF,
    RoundMenu,
    Action,
)

from ui.common.palette_utils import extract_adaptive_palette
from ui.components.chat_bubble import ImagePreviewDialog
from services.session_manager import SessionManager


class ThumbnailButton(QPushButton):
    """
    Интерактивная карточка миниатюры медиафайла в стиле Windows 11 Fluent Design.
    Поддерживает адаптивную палитру, тактильный Hover Lift и контекстное меню.
    """
    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self.image_path = Path(image_path)
        self.setFixedSize(148, 148)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        try:
            stat = self.image_path.stat()
            size_kb = stat.st_size / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
        except Exception:
            size_str = "—"
        self.setToolTip(f"{self.image_path.name}\nРазмер: {size_str}\n(Клик — просмотр • ПКМ — меню)")

        # Render thumbnail
        pix = QPixmap(str(self.image_path))
        if not pix.isNull():
            thumb = pix.scaled(
                136, 136,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.setIcon(QIcon(thumb))
            self.setIconSize(QSize(136, 136))

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def update_adaptive_palette(self, palette: Dict[str, str]):
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
                padding: 4px;
            }}
            QPushButton:hover {{
                border-color: {palette['accent']};
                background-color: {palette['card_hover']};
            }}
            QPushButton:pressed {{
                background-color: {palette['card']};
                border-color: {palette.get('secondary', palette['accent'])};
            }}
        """)

    def _show_context_menu(self, pos: QPoint):
        menu = RoundMenu(parent=self)

        act_view = Action(FIF.PHOTO, "Полноразмерный просмотр", self)
        act_view.triggered.connect(self.click)
        menu.addAction(act_view)

        act_copy = Action(FIF.COPY, "Копировать путь к файлу", self)
        act_copy.triggered.connect(self._copy_path)
        menu.addAction(act_copy)

        menu.addSeparator()

        act_explorer = Action(FIF.FOLDER, "Показать в Проводнике", self)
        act_explorer.triggered.connect(self._reveal_in_explorer)
        menu.addAction(act_explorer)

        menu.exec(self.mapToGlobal(pos))

    def _copy_path(self):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(str(self.image_path.resolve()))

    def _reveal_in_explorer(self):
        try:
            resolved = str(self.image_path.resolve())
            if hasattr(os, "startfile"):
                subprocess.Popen(f'explorer /select,"{resolved}"')
            else:
                subprocess.Popen(["explorer", "/select,", resolved])
        except Exception:
            pass


class ChatMediaGalleryDialog(FramelessDialog):
    """
    Диалоговое окно «Медиа галерея» NeonRoute в стиле Windows 11 Fluent Design.
    """
    def __init__(
        self,
        session_id: str,
        session_images_dir: Optional[Path] = None,
        image_paths: Optional[List[Path]] = None,
        session_title: Optional[str] = None,
        parent=None
    ):
        super().__init__(parent=parent)
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

        self.resize(800, 620)
        self.setMinimumSize(560, 420)

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
            self.titleBar.closeBtn.clicked.connect(self.reject)

        self.setObjectName("ChatMediaGalleryDialog")
        self._current_palette: Dict[str, str] = {}
        self._thumbnail_buttons: List[ThumbnailButton] = []

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
        """Адаптирует цветовую палитру окна, карточек и кнопок под активные обои."""
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            ChatMediaGalleryDialog#ChatMediaGalleryDialog {{
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
            QWidget#MediaGalleryBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #ChatMediaGalleryDialog QWidget {{
                background-color: transparent;
                color: #CBD5E1;
            }}
            #ChatMediaGalleryDialog QScrollArea {{
                background-color: transparent;
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QLabel#GalleryBadgeLabel {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
                padding: 3px 12px;
                font-weight: 600;
                font-size: 12px;
                color: {palette['accent']};
            }}
            SearchLineEdit {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 8px;
                color: #F8FAFC;
                padding: 4px 8px;
            }}
            SearchLineEdit:hover {{
                border-color: {palette['accent']};
            }}
            SearchLineEdit:focus {{
                border-color: {palette['accent']};
            }}
        """)

        for btn in self._thumbnail_buttons:
            btn.update_adaptive_palette(palette)

        if hasattr(self, "btn_open_folder") and self.btn_open_folder:
            self.btn_open_folder.setStyleSheet(f"""
                PushButton {{
                    background-color: {palette['card']};
                    color: #E2E8F0;
                    border: 1px solid {palette['card_border']};
                    border-radius: 8px;
                    padding: 7px 18px;
                    font-weight: 500;
                }}
                PushButton:hover {{
                    background-color: {palette['card_hover']};
                    border-color: {palette['accent']};
                    color: {palette['accent']};
                }}
            """)

        if hasattr(self, "btn_refresh") and self.btn_refresh:
            self.btn_refresh.setStyleSheet(f"""
                PushButton {{
                    background-color: {palette['card']};
                    color: #E2E8F0;
                    border: 1px solid {palette['card_border']};
                    border-radius: 8px;
                    padding: 7px 18px;
                    font-weight: 500;
                }}
                PushButton:hover {{
                    background-color: {palette['card_hover']};
                    border-color: {palette['accent']};
                    color: {palette['accent']};
                }}
            """)

        if hasattr(self, "btn_close") and self.btn_close:
            self.btn_close.setStyleSheet(f"""
                PushButton {{
                    background-color: {palette['accent']};
                    color: #0A0D14;
                    border: 1px solid {palette['accent']};
                    border-radius: 8px;
                    padding: 7px 22px;
                    font-weight: 600;
                }}
                PushButton:hover {{
                    background-color: #38BDF8;
                    border-color: #38BDF8;
                    color: #0A0D14;
                }}
            """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("MediaGalleryBodyWidget")
        layout = QVBoxLayout(self.body_widget)
        layout.setContentsMargins(20, 10, 20, 16)
        layout.setSpacing(12)

        # 1. Header Bar (Title, Badge, Search)
        hdr_layout = QHBoxLayout()
        header_text = "🖼️ Медиа галерея" if self.session_id == "all_sessions" else "🖼️ Медиафайлы диалога"
        self.title_lbl = SubtitleLabel(header_text, self.body_widget)
        self.title_lbl.setStyleSheet("font-weight: bold; font-size: 16px; color: #F8FAFC;")
        hdr_layout.addWidget(self.title_lbl)

        hdr_layout.addSpacing(10)

        self.badge_lbl = QLabel(f"{len(self.image_paths)} файлов", self.body_widget)
        self.badge_lbl.setObjectName("GalleryBadgeLabel")
        hdr_layout.addWidget(self.badge_lbl)

        hdr_layout.addStretch()

        self.search_edit = SearchLineEdit(self.body_widget)
        self.search_edit.setPlaceholderText("Поиск по имени файла...")
        self.search_edit.setFixedWidth(240)
        self.search_edit.textChanged.connect(self._filter_images)
        hdr_layout.addWidget(self.search_edit)

        layout.addLayout(hdr_layout)

        # 2. Scroll Area & Grid Container
        self.scroll = QScrollArea(self.body_widget)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)
        self.grid_layout.setSpacing(12)

        self._empty_label = QLabel(self.grid_container)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #64748B; font-size: 13px; margin: 40px;")
        self._empty_label.setText("В этом диалоге пока нет сгенерированных изображений.\nПопросите Ассистента создать арт или фото, и они появятся здесь!")
        self.grid_layout.addWidget(self._empty_label, 0, 0, 1, 4, Qt.AlignmentFlag.AlignCenter)

        self.scroll.setWidget(self.grid_container)
        layout.addWidget(self.scroll, stretch=1)

        self._populate_grid()

        # 3. Bottom Action Bar
        btn_layout = QHBoxLayout()

        self.btn_open_folder = PushButton("📂 Открыть папку в Проводнике", self.body_widget)
        self.btn_open_folder.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_open_folder.clicked.connect(self._open_folder)
        btn_layout.addWidget(self.btn_open_folder)

        self.btn_refresh = PushButton("🔄 Обновить", self.body_widget)
        self.btn_refresh.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_refresh.clicked.connect(self.refresh_gallery)
        btn_layout.addWidget(self.btn_refresh)

        btn_layout.addStretch()

        self.btn_close = PushButton("Закрыть", self.body_widget)
        self.btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)
        root_layout.addWidget(self.body_widget)

    def _populate_grid(self):
        """Наполняет сетку карточками миниатюр."""
        for btn in self._thumbnail_buttons:
            self.grid_layout.removeWidget(btn)
            btn.deleteLater()
        self._thumbnail_buttons.clear()

        total = len(self.image_paths)
        if hasattr(self, "badge_lbl"):
            self.badge_lbl.setText(f"{total} файлов")

        if not self.image_paths:
            self._empty_label.setVisible(True)
            self._empty_label.setText("В этом диалоге пока нет сгенерированных изображений.\nПопросите Ассистента создать арт или фото, и они появятся здесь!")
            return

        self._empty_label.setVisible(False)
        columns = 4
        for idx, img_p in enumerate(self.image_paths):
            row = idx // columns
            col = idx % columns
            btn = ThumbnailButton(img_p, self.grid_container)
            btn.clicked.connect(lambda checked, p=img_p: self._preview_image(p))
            if self._current_palette:
                btn.update_adaptive_palette(self._current_palette)
            self.grid_layout.addWidget(btn, row, col)
            self._thumbnail_buttons.append(btn)

    def _filter_images(self, query: str):
        """Фильтрует карточки изображений по введенному запросу."""
        query = (query or "").strip().lower()
        visible_count = 0
        for btn in self._thumbnail_buttons:
            matches = (not query) or (query in btn.image_path.name.lower())
            btn.setVisible(matches)
            if matches:
                visible_count += 1

        total = len(self.image_paths)
        if query:
            self.badge_lbl.setText(f"{visible_count} из {total}")
        else:
            self.badge_lbl.setText(f"{total} файлов")

        if visible_count == 0:
            self._empty_label.setVisible(True)
            if total > 0:
                self._empty_label.setText(f"По запросу «{query}» ничего не найдено.")
            else:
                self._empty_label.setText("В этом диалоге пока нет сгенерированных изображений.\nПопросите Ассистента создать арт или фото, и они появятся здесь!")
        else:
            self._empty_label.setVisible(False)

    def refresh_gallery(self):
        """Повторно сканирует директорию медиафайлов и обновляет галерею."""
        found_paths = []
        if self.session_images_dir.exists():
            if self.session_id == "all_sessions":
                from config import OUTPUTS_DIR
                sessions_dir = OUTPUTS_DIR / "sessions"
                if sessions_dir.exists():
                    for img_p in sessions_dir.rglob("*"):
                        if img_p.is_file() and img_p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                            found_paths.append(img_p)
                for img_p in OUTPUTS_DIR.glob("*"):
                    if img_p.is_file() and img_p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        if img_p not in found_paths:
                            found_paths.append(img_p)
            else:
                for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                    found_paths.extend(list(self.session_images_dir.glob(ext)))

        found_paths.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
        self.image_paths = found_paths
        self._populate_grid()
        self.update_adaptive_palette()

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
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)
