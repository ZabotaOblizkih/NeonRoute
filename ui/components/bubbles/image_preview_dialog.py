"""
Full-resolution image preview modal dialog with system viewer integration.
Modernized to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Dynamic Adaptive Palette).
Supports generation parameters accordion inspection (GenerationInfoAccordionWidget).
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QApplication, QScrollArea, QSizePolicy
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
)

from ui.common.palette_utils import extract_adaptive_palette
from ui.components.bubbles.workspace_cards import GenerationInfoAccordionWidget


class ImagePreviewDialog(FramelessDialog):
    """
    Модальное окно полноразмерного просмотра изображений в стиле Windows 11 Fluent Design.
    Адаптирует цветовую палитру под активные обои интерфейса (Material You / Fluent Tint)
    с нативным FluentTitleBar, скруглёнными углами и компонентами QFluentWidgets.
    Включает в себя перенесённое меню с параметрами генерации (GenerationInfoAccordionWidget).
    """
    def __init__(self, image_path: Path, parent=None, generation_params: Optional[dict] = None):
        super().__init__(parent=parent)
        self.image_path = Path(image_path)
        self.setObjectName("ImagePreviewDialog")

        # Разрешение параметров генерации: переданные -> от родителя -> из метаданных PNG
        self.generation_params = generation_params or getattr(parent, "generation_params", None)
        if not self.generation_params:
            self.generation_params = self._extract_png_metadata_params(self.image_path)

        self._img_pixmap = QPixmap(str(self.image_path))
        orig_w = self._img_pixmap.width() if not self._img_pixmap.isNull() else 0
        orig_h = self._img_pixmap.height() if not self._img_pixmap.isNull() else 0
        file_sz_kb = self.image_path.stat().st_size / 1024 if self.image_path.exists() else 0
        sz_str = f"{file_sz_kb / 1024:.2f} MB" if file_sz_kb > 1024 else f"{file_sz_kb:.0f} KB"
        ext = self.image_path.suffix.lstrip(".").upper() or "IMG"

        self._orig_w = orig_w
        self._orig_h = orig_h
        self._sz_str = sz_str
        self._ext = ext

        self.setWindowTitle(f"Просмотр — {self.image_path.name}")

        # 1. Fluent TitleBar с [X] в правом верхнем углу
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(44)
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

        self._current_palette: Dict[str, str] = {}
        self._setup_ui()
        self.update_adaptive_palette()

    @staticmethod
    def _extract_png_metadata_params(image_path: Path) -> Optional[dict]:
        """Извлекает параметры генерации SD Forge / WebUI из PNG tEXt chunks при их наличии."""
        try:
            if not image_path.exists() or image_path.suffix.lower() != ".png":
                return None
            from PIL import Image
            with Image.open(image_path) as img:
                raw_params = img.info.get("parameters")
                if not raw_params or not isinstance(raw_params, str):
                    return None

                lines = raw_params.strip().split("\n")
                pos_prompt = []
                neg_prompt = []
                settings_line = ""
                in_neg = False
                for line in lines:
                    line_s = line.strip()
                    if line_s.startswith("Negative prompt:"):
                        in_neg = True
                        neg_prompt.append(line_s[len("Negative prompt:"):].strip())
                    elif "Steps:" in line_s and "Sampler:" in line_s:
                        settings_line = line_s
                        in_neg = False
                    elif in_neg:
                        neg_prompt.append(line_s)
                    else:
                        pos_prompt.append(line_s)

                parsed: Dict[str, Any] = {
                    "positive_prompt": "\n".join(pos_prompt).strip(),
                    "negative_prompt": "\n".join(neg_prompt).strip(),
                }
                for part in settings_line.split(", "):
                    if ":" in part:
                        k, v = part.split(":", 1)
                        k = k.strip().lower()
                        v = v.strip()
                        if k == "steps":
                            parsed["steps"] = v
                        elif k == "sampler":
                            parsed["sampler_name"] = v
                        elif k == "seed":
                            parsed["seed"] = v
                        elif k == "model":
                            parsed["checkpoint"] = v
                        elif k == "denoising strength":
                            parsed["denoising_strength"] = v
                return parsed
        except Exception:
            return None

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
            p = p.parent() if hasattr(p, "parent") else None
        return None

    def update_adaptive_palette(self):
        """Адаптирует цветовую палитру окна, контейнеров и кнопок под активные обои."""
        pixmap = self._resolve_pixmap()
        if not pixmap or pixmap.isNull():
            pixmap = self._img_pixmap

        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            ImagePreviewDialog#ImagePreviewDialog {{
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
            QWidget#ImagePreviewBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #ImagePreviewDialog QWidget {{
                background-color: transparent;
            }}
            QFrame#ImageFrameContainer {{
                background-color: {palette['terminal_bg']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QLabel#InfoBadgeLabel {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                color: {palette['text_secondary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
                font-weight: 500;
                padding: 4px 10px;
            }}
        """)

        if hasattr(self, "gen_info_widget") and self.gen_info_widget:
            self.gen_info_widget.update_adaptive_palette(palette)

        if hasattr(self, "btn_open_sys") and self.btn_open_sys:
            self.btn_open_sys.setStyleSheet(f"""
                PrimaryPushButton {{
                    background-color: {palette['accent']};
                    border: 1px solid {palette['accent']};
                    border-radius: 6px;
                    color: #0A0D14;
                    font-weight: 600;
                    font-size: 12px;
                    padding: 7px 18px 7px 38px;
                    min-height: 20px;
                }}
                PrimaryPushButton:hover {{
                    background-color: {palette['btn_hover']};
                    border-color: {palette['btn_hover']};
                    color: {palette['btn_hover_text']};
                }}
            """)

        if hasattr(self, "btn_show_folder") and self.btn_show_folder:
            self.btn_show_folder.setStyleSheet(f"""
                PushButton {{
                    background-color: {palette['card']};
                    color: #E2E8F0;
                    border: 1px solid {palette['card_border']};
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: 500;
                    padding: 7px 16px 7px 36px;
                    min-height: 20px;
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
                    background-color: {palette['card']};
                    color: #94A3B8;
                    border: 1px solid {palette['card_border']};
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: 500;
                    padding: 7px 16px;
                    min-height: 20px;
                }}
                PushButton:hover {{
                    background-color: {palette['card_hover']};
                    border-color: {palette['border']};
                    color: #F8FAFC;
                }}
            """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("ImagePreviewBodyWidget")
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(16, 8, 16, 14)
        body_layout.setSpacing(10)

        # 1. Info Badge Row
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(8)

        info_text = f"🖼️ {self._orig_w} × {self._orig_h} px  •  {self._sz_str}  •  {self._ext} Full-res"
        self.info_badge = QLabel(info_text, self.body_widget)
        self.info_badge.setObjectName("InfoBadgeLabel")
        hdr_layout.addWidget(self.info_badge)
        hdr_layout.addStretch()
        body_layout.addLayout(hdr_layout)

        # 2. Image Container Frame
        img_frame = QFrame(self.body_widget)
        img_frame.setObjectName("ImageFrameContainer")
        img_frame_layout = QVBoxLayout(img_frame)
        img_frame_layout.setContentsMargins(6, 6, 6, 6)
        img_frame_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.img_label = QLabel(img_frame)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setStyleSheet("background: transparent; border: none;")

        screen = QApplication.primaryScreen()
        avail_rect = screen.availableGeometry() if screen else None
        if avail_rect:
            max_w = int(avail_rect.width() * 0.85)
            max_h = int(avail_rect.height() * 0.78)
        else:
            max_w = 1600
            max_h = 900

        avail_img_w = max_w - 64
        if self.generation_params:
            avail_img_h = max(240, int(avail_rect.height() * 0.75) - 300) if avail_rect else 550
        else:
            avail_img_h = max(300, int(avail_rect.height() * 0.78) - 130) if avail_rect else 700

        if not self._img_pixmap.isNull():
            orig_w = self._img_pixmap.width()
            orig_h = self._img_pixmap.height()
            scale_factor = min(1.0, avail_img_w / max(1, orig_w), avail_img_h / max(1, orig_h))
            if scale_factor < 1.0:
                scaled_w = max(1, int(orig_w * scale_factor))
                scaled_h = max(1, int(orig_h * scale_factor))
                scaled = self._img_pixmap.scaled(
                    scaled_w, scaled_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            else:
                scaled = self._img_pixmap
            self.img_label.setPixmap(scaled)
            extra_h = 130 if self.generation_params else 110
            target_w = max(560, min(scaled.width() + 48, max_w))
            target_h = max(380, min(scaled.height() + extra_h, max_h))
            self.resize(target_w, target_h)
        else:
            self.img_label.setText("Не удалось загрузить изображение")
            self.img_label.setStyleSheet("color: #EF4444; font-size: 13px;")
            self.resize(560, 420)

        img_frame_layout.addWidget(self.img_label)
        body_layout.addWidget(img_frame, stretch=1)

        # 2.5. Перенесённое меню «Инфо генерации»
        self.gen_info_widget: Optional[GenerationInfoAccordionWidget] = None
        if self.generation_params:
            self.gen_info_widget = GenerationInfoAccordionWidget(
                self.generation_params,
                parent=self.body_widget
            )
            self.gen_info_widget.toggled.connect(self._on_gen_info_toggled)
            body_layout.addWidget(self.gen_info_widget)

        # 3. Actions Row
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_open_sys = PrimaryPushButton(FIF.PHOTO, "Открыть оригинал в Windows", self.body_widget)
        self.btn_open_sys.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_open_sys.setToolTip("Запустить исходный файл без потерь в штатном просмотрщике Windows")
        self.btn_open_sys.clicked.connect(self._open_in_windows)

        self.btn_show_folder = PushButton(FIF.FOLDER, "Показать в папке", self.body_widget)
        self.btn_show_folder.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_show_folder.setToolTip("Открыть проводник Windows и выделить файл")
        self.btn_show_folder.clicked.connect(self._show_in_folder)

        self.btn_close = PushButton("Закрыть", self.body_widget)
        self.btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_close.clicked.connect(self.reject)

        btn_layout.addWidget(self.btn_open_sys)
        btn_layout.addWidget(self.btn_show_folder)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        body_layout.addLayout(btn_layout)

        root_layout.addWidget(self.body_widget)

        # Фиксируем точный базовый размер после компоновки всех виджетов
        self.adjustSize()
        self._base_dialog_size = self.size()
        if avail_rect:
            geo = self.frameGeometry()
            geo.moveCenter(avail_rect.center())
            self.move(geo.topLeft())

    def _on_gen_info_toggled(self, is_expanded: bool):
        """Реактивно адаптирует высоту окна, возвращая исходный размер и защищая от улетания под Пуск."""
        screen = self.screen() or QApplication.primaryScreen()
        avail_rect = screen.availableGeometry() if screen else None

        if not is_expanded:
            # Сворачиваем обратно строго в базовый компактный размер
            self.body_widget.adjustSize()
            self.adjustSize()
            if hasattr(self, "_base_dialog_size") and self._base_dialog_size.isValid():
                self.resize(self._base_dialog_size)
            if avail_rect:
                geo = self.frameGeometry()
                geo.moveCenter(avail_rect.center())
                self.move(geo.topLeft())
        else:
            # Раскрываем контент вниз без раздувания ширины окна
            content_h = self.gen_info_widget.content_frame.sizeHint().height() if self.gen_info_widget else 200
            max_screen_h = (avail_rect.height() - 24) if avail_rect else 900
            new_h = min(self._base_dialog_size.height() + max(180, content_h) + 12, max_screen_h)
            self.resize(self._base_dialog_size.width(), new_h)

            if avail_rect:
                curr_bottom = self.y() + self.height()
                max_bottom = avail_rect.bottom() - 14
                if curr_bottom > max_bottom:
                    excess = curr_bottom - max_bottom
                    new_y = max(avail_rect.top() + 10, self.y() - excess)
                    self.move(self.x(), new_y)

    def _open_in_windows(self):
        try:
            if self.image_path.exists():
                os.startfile(str(self.image_path))
        except Exception:
            pass

    def _show_in_folder(self):
        try:
            if self.image_path.exists():
                subprocess.Popen(f'explorer /select,"{self.image_path}"')
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self.update_adaptive_palette()
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            self.adjustSize()
            self._base_dialog_size = self.size()
            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                avail_rect = screen.availableGeometry()
                geo = self.frameGeometry()
                geo.moveCenter(avail_rect.center())
                self.move(geo.topLeft())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
