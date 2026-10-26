"""
Chat Message Bubble Component for NeonRoute
Supports rich Markdown rendering, inline image thumbnails with full-preview modal,
interactive File Cards, embedded Audio Player, and quick action buttons.
Redesigned in Telegram/WhatsApp Glassmorphic bubble architecture with circular interactive avatars.
"""

import html
import re
import os
import sys
import math
import threading
import logging
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Union, Any, Dict

from config import WORKSPACE_DIR
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QRectF, QUrl, QTimer, QSize, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PyQt6.QtGui import (
    QTextOption,
    QPixmap, QCursor, QIcon, QPainter, QPainterPath,
    QColor, QFont, QLinearGradient, QRadialGradient, QBrush, QPen, QDesktopServices,
    QFocusEvent, QImageReader
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser,
    QPushButton, QDialog, QApplication, QFrame, QMenu, QLayout,
    QSizePolicy, QGraphicsDropShadowEffect, QGraphicsOpacityEffect
)
from qfluentwidgets import (
    TransparentToolButton,
    FluentIcon as FIF,
    ToolTipFilter,
    ToolTipPosition,
)

logger = logging.getLogger("NeonRoute.ChatBubble")

from ui.components.audio_player import AudioPlayerWidget
from ui.components.file_card import FileCardWidget

class SelectableTextBrowser(QTextBrowser):
    """
    Subclass of QTextBrowser that automatically clears text selection
    when losing focus and strictly prevents horizontal scrolling/dragging.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def wheelEvent(self, event):
        """Пересылает прокрутку мыши родителю, исключая внутренний скроллинг."""
        event.ignore()

    def scrollContentsBy(self, dx: int, dy: int):
        super().scrollContentsBy(0, dy)

    def focusOutEvent(self, event: QFocusEvent):
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.clearSelection()
            self.setTextCursor(cursor)
        super().focusOutEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            doc = self.document()
            w = max(100.0, float(self.viewport().width()))
            if abs(doc.textWidth() - w) > 6:
                doc.setTextWidth(w)


# Facade Re-export (Zero-Breaking-Changes)
from ui.components.bubbles.avatar_renderer import (
    render_circular_avatar,
    render_rounded_art_pixmap,
    get_cached_art_pixmap,
    _IMAGE_CACHE_LOCK,
    _IMAGE_PREVIEW_CACHE,
)
from ui.components.bubbles.image_preview_dialog import ImagePreviewDialog
from ui.components.bubbles.workspace_cards import (
    WorkspaceFileCardWidget,
    ConsoleOutputSpoilerWidget,
    GenerationInfoAccordionWidget,
)


class ChatMessageWidget(QWidget):
    tts_requested = pyqtSignal(str)
    translate_requested = pyqtSignal(str, object)
    regenerate_requested = pyqtSignal(object)
    delete_requested = pyqtSignal(object)
    scene_art_requested = pyqtSignal(str, object)
    run_script_requested = pyqtSignal(str)
    choice_selected = pyqtSignal(str)

    def __init__(
        self,
        sender: str,
        text: str,
        is_user: bool = False,
        is_error: bool = False,
        image_path: Optional[Path] = None,
        file_paths: Optional[List[Path]] = None,
        audio_path: Optional[Path] = None,
        avatar_path: Optional[Path] = None,
        translated_text: Optional[str] = None,
        is_showing_translation: bool = False,
        font_size: int = 13,
        negative_prompt: Optional[str] = None,
        timestamp: Optional[str] = None,
        is_roleplay: bool = False,
        generation_params: Optional[dict] = None,
        message_id: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        visual_intent: Optional[Union[dict, Any]] = None,
        parent=None,
        **kwargs
    ):
        super().__init__(parent)
        import uuid
        self.message_id = message_id or str(uuid.uuid4())
        self.reply_to_id = reply_to_id
        self.sender = sender
        self.raw_text = text
        self.original_text = text
        self.translated_text = translated_text
        self.is_showing_translation = is_showing_translation
        self.font_size = font_size
        self.is_user = is_user
        self.is_error = is_error
        self.is_roleplay = is_roleplay
        self.negative_prompt = negative_prompt
        self.generation_params = generation_params
        if self.negative_prompt:
            if not self.generation_params:
                self.generation_params = {}
            if "negative_prompt" not in self.generation_params:
                self.generation_params["negative_prompt"] = self.negative_prompt
        self.gen_info_widget: Optional[GenerationInfoAccordionWidget] = None
        self.image_path = Path(image_path) if image_path else None
        self.file_paths = [Path(f) for f in (file_paths or [])]
        self.audio_path = Path(audio_path) if audio_path else None
        self.avatar_path = Path(avatar_path) if avatar_path else None
        raw_ts = timestamp or datetime.now().strftime("%H:%M")
        if "T" in raw_ts:
            try:
                # Извлекаем чистое время HH:MM из ISO строки
                time_part = raw_ts.split("T")[1]
                self.timestamp = time_part[:5]
            except Exception:
                self.timestamp = raw_ts[:5]
        else:
            self.timestamp = raw_ts
        self.negative_badge: Optional[QWidget] = None
        self.audio_widget: Optional[AudioPlayerWidget] = None
        self.btn_translate: Optional[TransparentToolButton] = None
        self.btn_scene_art: Optional[TransparentToolButton] = None
        self.copy_btn: Optional[TransparentToolButton] = None
        self.tts_btn: Optional[TransparentToolButton] = None
        self.btn_regen: Optional[TransparentToolButton] = None
        self.btn_del: Optional[TransparentToolButton] = None
        self.bubble_container: Optional[QFrame] = None
        self.avatar_lbl: Optional[QLabel] = None
        self.text_browser: Optional[SelectableTextBrowser] = None
        self.art_proposal_chip: Optional[QPushButton] = None
        self.art_placeholder: Optional[CyberpunkArtPlaceholderWidget] = None
        self.footer_container: Optional[QWidget] = None
        self.footer_layout: Optional[QHBoxLayout] = None
        self.visual_intent: Optional[Union[dict, Any]] = visual_intent
        self.rpg_status_widget = None
        self.rpg_thought_widget = None
        self.rpg_choices_widget = None
        self.knowledge_sources: List[Dict[str, Any]] = kwargs.get("knowledge_sources", [])
        self.knowledge_hud = None
        self.knowledge_hud_container = None
        self.thought_spoiler = None
        self.thought_data = kwargs.get("thought", kwargs.get("thought_data", None))

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background: transparent;")
        
        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(12, 4, 12, 8)
        outer_layout.setSpacing(14)
        outer_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.avatar_lbl = QLabel(self)
        self.avatar_lbl.setFixedSize(64, 64)
        self.avatar_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_lbl.setStyleSheet("background: transparent;")
        avatar_pix = render_circular_avatar(
            avatar_path=self.avatar_path,
            name=self.sender,
            size=64,
            is_user=self.is_user,
            is_error=self.is_error
        )
        self.avatar_lbl.setPixmap(avatar_pix)

        self.bubble_container = QFrame(self)
        self.bubble_container.setObjectName("BubbleContainer")
        self.bubble_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.bubble_frame = self.bubble_container
        self.bubble_container.setProperty("is_user", "true" if self.is_user else "false")
        self.bubble_container.setProperty("is_error", "true" if self.is_error else "false")

        # QGraphicsDropShadowEffect удален ради 60 FPS при скролле (глубина задается через QSS border-bottom)

        if self.is_error:
            self.bubble_container.setStyleSheet("""
                #BubbleContainer, #bubble_frame {
                    background-color: rgba(45, 17, 23, 0.85);
                    border: 1px solid rgba(239, 68, 68, 0.40);
                    border-radius: 13px;
                    padding: 8px 12px;
                }
            """)
        elif self.is_user:
            self.bubble_container.setStyleSheet("""
                #BubbleContainer, #bubble_frame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(0, 140, 160, 0.45), stop:1 rgba(0, 80, 110, 0.55));
                    border: 1px solid rgba(0, 240, 255, 0.28);
                    border-radius: 13px;
                    padding: 8px 12px;
                }
            """)
        else:
            self.bubble_container.setStyleSheet("""
                #BubbleContainer, #bubble_frame {
                    background-color: rgba(17, 22, 34, 0.75);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 13px;
                    padding: 8px 12px;
                }
            """)

        bubble_layout = QVBoxLayout(self.bubble_container)
        bubble_layout.setContentsMargins(6, 6, 6, 6)
        bubble_layout.setSpacing(6)
        bubble_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        if self.is_error:
            name_color = "#EF4444"
        elif self.is_user:
            name_color = "#E0E7FF"
        else:
            name_color = "#818CF8"

        name_lbl = QLabel(self.sender, self.bubble_container)
        name_lbl.setStyleSheet(f"color: {name_color}; font-family: 'Inter', 'Segoe UI', sans-serif; font-weight: bold; font-size: 12px; background: transparent;")
        header_layout.addWidget(name_lbl)
        header_layout.addStretch()

        bubble_layout.addLayout(header_layout)





        is_art_caption = bool(
            self.image_path and self.raw_text and (
                "🎨 Арт-директор" in self.raw_text or
                "Изображение успешно сгенерировано" in self.raw_text
            )
        )

        if self.raw_text and not is_art_caption:
            self.text_browser = SelectableTextBrowser(self.bubble_container)
            self.text_browser.setReadOnly(True)
            self.text_browser.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            self.text_browser.setOpenExternalLinks(False)
            self.text_browser.setOpenLinks(False)
            self.text_browser.anchorClicked.connect(self._on_anchor_clicked)
            self.text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.text_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.text_browser.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse |
                Qt.TextInteractionFlag.TextSelectableByKeyboard |
                Qt.TextInteractionFlag.LinksAccessibleByMouse |
                Qt.TextInteractionFlag.LinksAccessibleByKeyboard
            )
            self.text_browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.text_browser.customContextMenuRequested.connect(self._show_context_menu)

            font = QFont("Inter", self.font_size)
            font.setFamilies(["Inter", "Segoe UI", "sans-serif"])
            self.text_browser.setFont(font)
            if self.text_browser.document():
                self.text_browser.document().setDefaultFont(font)

            text_color = "#FCA5A5" if self.is_error else ("#FFFFFF" if self.is_user else "#F1F5F9")
            self.text_browser.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: transparent;
                    color: {text_color};
                    border: none;
                    font-family: 'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
                    font-size: {self.font_size}px;
                    line-height: 1.18;
                    selection-background-color: #00F0FF;
                    selection-color: #000000;
                }}
            """)
            initial_text = self.translated_text if (self.is_showing_translation and self.translated_text) else self.raw_text
            html_content = self._markdown_to_html(initial_text)
            self.text_browser.setHtml(html_content)
            bubble_layout.addWidget(self.text_browser)
            self._adjust_text_height()



        from ui.components.chat_message_widget import KnowledgeHUDWidget
        self.knowledge_hud = KnowledgeHUDWidget(self.bubble_container)
        self.knowledge_hud_container = self.knowledge_hud
        bubble_layout.addWidget(self.knowledge_hud)
        if self.knowledge_sources:
            self.knowledge_hud.set_sources(self.knowledge_sources)

        from ui.components.thought_spoiler_widget import ThoughtSpoilerWidget
        self.thought_spoiler = ThoughtSpoilerWidget(self.bubble_container)
        bubble_layout.addWidget(self.thought_spoiler)
        self.thought_spoiler.setVisible(False)
        self.thought_spoiler.hide()
        if self.thought_data and isinstance(self.thought_data, dict):
            self.set_thought_data(
                text=self.thought_data.get("content", ""),
                duration=float(self.thought_data.get("duration", 0.0)),
                token_count=int(self.thought_data.get("token_count", 0))
            )

        self.image_preview_lbl = QLabel(self.bubble_container)
        self.image_preview_lbl.setObjectName("MessageImagePreview")
        self.image_preview_lbl.setVisible(False)
        self.art_container = None
        self.art_layout = None

        if self.image_path and self.image_path.exists():
            rounded_pix = get_cached_art_pixmap(self.image_path, target_size=380, radius=14.0)
            if not rounded_pix.isNull():
                img_sz = QImageReader(str(self.image_path)).size()
                orig_w, orig_h = (img_sz.width(), img_sz.height()) if img_sz.isValid() else (rounded_pix.width(), rounded_pix.height())
                scaled_pix = rounded_pix
                file_sz_kb = self.image_path.stat().st_size / 1024 if self.image_path.exists() else 0
                sz_str = f"{file_sz_kb / 1024:.2f} MB" if file_sz_kb > 1024 else f"{file_sz_kb:.0f} KB"

                self.art_container = QWidget(self.bubble_container)
                self.art_container.setObjectName("ArtContainer")
                self.art_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
                self.art_container.setStyleSheet("background: transparent; border: none;")
                self.art_container.setMinimumWidth(rounded_pix.width())

                self.art_layout = QVBoxLayout(self.art_container)
                self.art_layout.setContentsMargins(0, 0, 0, 0)
                self.art_layout.setSpacing(4)
                self.art_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

                self.image_preview_lbl.setParent(self.art_container)
                self.image_preview_lbl.setFixedSize(rounded_pix.size())
                self.image_preview_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                self.image_preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.image_preview_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                self.image_preview_lbl.setStyleSheet("background: transparent; border: none;")
                self.image_preview_lbl.setPixmap(rounded_pix)

                model_info = ""
                if self.raw_text:
                    m_match = re.search(r'\((.*?)\)', self.raw_text)
                    if m_match:
                        model_info = m_match.group(1).strip()
                    elif "🎨 Арт-директор" in self.raw_text:
                        model_info = self.raw_text.replace("🎨 Арт-директор", "").strip(" :()")

                tooltip_lines = [
                    f"Оригинал: {orig_w}x{orig_h} px • {sz_str}",
                    f"Модель: {model_info}" if model_info else "",
                    "Клик — полноразмерный просмотр"
                ]
                self.image_preview_lbl.setToolTip("\n".join(filter(None, tooltip_lines)))
                self.image_preview_lbl.mousePressEvent = lambda e: self._open_image_preview(self.image_path) if (not hasattr(e, "button") or e.button() == Qt.MouseButton.LeftButton) else None
                self.image_preview_lbl.setVisible(True)
                self.art_layout.addWidget(self.image_preview_lbl)

                if model_info:
                    model_badge = QLabel(f"🎨 Модель: {model_info}", self.art_container)
                    model_badge.setObjectName("ArtModelBadge")
                    model_badge.setMaximumWidth(scaled_pix.width())
                    model_badge.setWordWrap(True)
                    model_badge.setStyleSheet("""
                        QLabel#ArtModelBadge {
                            color: #38BDF8;
                            font-size: 11px;
                            font-weight: 600;
                            background: transparent;
                            border: none;
                            padding: 1px 2px;
                        }
                    """)
                    model_badge.setToolTip(f"🎨 Модель генерации: {model_info}")
                    self.art_layout.addWidget(model_badge)

                if self.generation_params:
                    self._render_generation_info_accordion(self.art_layout, max_width=None)

                bubble_layout.addWidget(self.art_container)

                if is_art_caption:
                    self.bubble_container.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
            else:
                self.image_preview_lbl.setVisible(False)
        else:
            self.image_preview_lbl.setVisible(False)

        for f_path in self.file_paths:
            if f_path.exists() and f_path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.wav'):
                card = FileCardWidget(f_path, self.bubble_container)
                bubble_layout.addWidget(card)

        if self.audio_path and self.audio_path.exists():
            self.audio_widget = AudioPlayerWidget(self.audio_path, self.bubble_container)
            bubble_layout.addWidget(self.audio_widget)

        self.footer_container = QWidget(self)
        footer_layout = QHBoxLayout(self.footer_container)
        footer_layout.setContentsMargins(4, 2, 4, 0)
        footer_layout.setSpacing(4)
        self.footer_layout = footer_layout

        self.copy_btn = TransparentToolButton(FIF.COPY, self.footer_container)
        self.copy_btn.setFixedSize(24, 24)
        self.copy_btn.setIconSize(QSize(13, 13))
        self.copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.copy_btn.setToolTip("Копировать текст")
        self.copy_btn.installEventFilter(ToolTipFilter(self.copy_btn, 300, ToolTipPosition.BOTTOM))
        self.copy_btn.clicked.connect(self._copy_text)
        footer_layout.addWidget(self.copy_btn)

        if not self.is_user and not self.is_error:
            self.tts_btn = TransparentToolButton(FIF.VOLUME, self.footer_container)
            self.tts_btn.setFixedSize(24, 24)
            self.tts_btn.setIconSize(QSize(13, 13))
            self.tts_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.tts_btn.setToolTip("Озвучить сообщение (F5-TTS)")
            self.tts_btn.installEventFilter(ToolTipFilter(self.tts_btn, 300, ToolTipPosition.BOTTOM))
            self.tts_btn.clicked.connect(lambda: self.tts_requested.emit(self.raw_text))
            footer_layout.addWidget(self.tts_btn)

            trans_tip = "Показать оригинал" if (self.is_showing_translation and self.translated_text) else "Перевести на русский"
            self.btn_translate = TransparentToolButton(FIF.LANGUAGE, self.footer_container)
            self.btn_translate.setFixedSize(24, 24)
            self.btn_translate.setIconSize(QSize(13, 13))
            self.btn_translate.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.btn_translate.setToolTip(trans_tip)
            self.btn_translate.installEventFilter(ToolTipFilter(self.btn_translate, 300, ToolTipPosition.BOTTOM))
            self.btn_translate.clicked.connect(self._toggle_translation)
            footer_layout.addWidget(self.btn_translate)

            if getattr(self, "is_roleplay", False):
                self.btn_scene_art = TransparentToolButton(FIF.PALETTE, self.footer_container)
                self.btn_scene_art.setFixedSize(24, 24)
                self.btn_scene_art.setIconSize(QSize(13, 13))
                self.btn_scene_art.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                self.btn_scene_art.setStyleSheet("TransparentToolButton:hover { background-color: rgba(192, 132, 252, 0.20); }")
                self.btn_scene_art.setToolTip("Создать иллюстрацию сцены (SD WebUI Forge)")
                self.btn_scene_art.installEventFilter(ToolTipFilter(self.btn_scene_art, 300, ToolTipPosition.BOTTOM))
                self.btn_scene_art.clicked.connect(lambda: self._on_scene_art_click(self.btn_scene_art))
                footer_layout.addWidget(self.btn_scene_art)

                # Если кадр сцены еще не готов — переводим кнопку в ожидание
                if not self.visual_intent:
                    self.set_scene_art_state(is_ready=False)
                else:
                    cue_t = self.visual_intent.get("prompt", "") if isinstance(self.visual_intent, dict) else getattr(self.visual_intent, "prompt", "")
                    self.set_scene_art_state(is_ready=True, cue_text=cue_t)

            self.btn_regen = TransparentToolButton(FIF.SYNC, self.footer_container)
            self.btn_regen.setFixedSize(24, 24)
            self.btn_regen.setIconSize(QSize(13, 13))
            self.btn_regen.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.btn_regen.setStyleSheet("TransparentToolButton:hover { background-color: rgba(251, 191, 36, 0.20); }")
            self.btn_regen.setToolTip("Повторить этот ответ")
            self.btn_regen.installEventFilter(ToolTipFilter(self.btn_regen, 300, ToolTipPosition.BOTTOM))
            self.btn_regen.clicked.connect(lambda: self.regenerate_requested.emit(self))
            footer_layout.addWidget(self.btn_regen)

        self.btn_del = TransparentToolButton(FIF.DELETE, self.footer_container)
        self.btn_del.setFixedSize(24, 24)
        self.btn_del.setIconSize(QSize(13, 13))
        self.btn_del.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_del.setStyleSheet("TransparentToolButton:hover { background-color: rgba(239, 68, 68, 0.20); }")
        self.btn_del.setToolTip("Удалить это сообщение")
        self.btn_del.installEventFilter(ToolTipFilter(self.btn_del, 300, ToolTipPosition.BOTTOM))
        self.btn_del.clicked.connect(lambda: self.delete_requested.emit(self))
        footer_layout.addWidget(self.btn_del)

        footer_layout.addStretch()

        self.time_lbl = QLabel(self.timestamp, self.footer_container)
        self.time_lbl.setStyleSheet("color: rgba(148, 163, 184, 0.60); font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 10px; background: transparent;")
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer_layout.addWidget(self.time_lbl)

        self.bubble_container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bubble_container.customContextMenuRequested.connect(self._show_context_menu)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Оборачиваем бабл и его независимые кнопки в вертикальную колонку
        msg_column = QVBoxLayout()
        msg_column.setContentsMargins(0, 0, 0, 0)
        msg_column.setSpacing(2)

        if self.is_user:
            msg_column.addWidget(self.bubble_container, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
            msg_column.addWidget(self.footer_container, alignment=Qt.AlignmentFlag.AlignRight)
            outer_layout.addStretch(1)
            outer_layout.addLayout(msg_column)
            outer_layout.addWidget(self.avatar_lbl, alignment=Qt.AlignmentFlag.AlignTop)
        else:
            msg_column.addWidget(self.bubble_container, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            msg_column.addWidget(self.footer_container, alignment=Qt.AlignmentFlag.AlignLeft)
            outer_layout.addWidget(self.avatar_lbl, alignment=Qt.AlignmentFlag.AlignTop)
            outer_layout.addLayout(msg_column)
            outer_layout.addStretch(1)

        self._update_bubble_geometry()

    def append_stream_chunk(self, chunk: str):
        """Плавное добавление потокового токена в видимый бабл без лагов перерисовки."""
        if getattr(self, "footer_container", None) and self.footer_container.isVisible():
            self.footer_container.setVisible(False)
        if not chunk:
            return
        self.raw_text += chunk
        self.original_text += chunk

        if not self.text_browser and self.bubble_container:
            self.text_browser = SelectableTextBrowser(self.bubble_container)
            self.text_browser.setReadOnly(True)
            self.text_browser.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            self.text_browser.setOpenExternalLinks(False)
            self.text_browser.setOpenLinks(False)
            self.text_browser.anchorClicked.connect(self._on_anchor_clicked)
            self.text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.text_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.text_browser.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse |
                Qt.TextInteractionFlag.TextSelectableByKeyboard |
                Qt.TextInteractionFlag.LinksAccessibleByMouse |
                Qt.TextInteractionFlag.LinksAccessibleByKeyboard
            )
            self.text_browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.text_browser.customContextMenuRequested.connect(self._show_context_menu)

            font = QFont("Inter", self.font_size)
            font.setFamilies(["Inter", "Segoe UI", "sans-serif"])
            self.text_browser.setFont(font)
            if self.text_browser.document():
                self.text_browser.document().setDefaultFont(font)

            text_color = "#FCA5A5" if self.is_error else ("#FFFFFF" if self.is_user else "#F1F5F9")
            self.text_browser.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: transparent;
                    color: {text_color};
                    border: none;
                    font-family: 'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
                    font-size: {self.font_size}px;
                    line-height: 1.18;
                    selection-background-color: #00F0FF;
                    selection-color: #000000;
                }}
            """)
            layout_ref = getattr(self, "bubble_layout", None) or (self.bubble_container.layout() if self.bubble_container else None)
            if layout_ref:
                # Текст ответа всегда монтируется на первую позицию под заголовок (индекс 1)
                layout_ref.insertWidget(1, self.text_browser)

        if self.text_browser:
            self.text_browser.setHtml(self._markdown_to_html(self.raw_text))
            self._update_bubble_geometry()
            self._adjust_text_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_bubble_geometry()
        self._adjust_text_height()

    def _refresh_layout(self):
        """Безопасный перерасчет геометрии и высоты документа без реэнтерабельного processEvents."""
        self._update_bubble_geometry()
        if self.bubble_container and self.bubble_container.layout():
            self.bubble_container.layout().activate()
        self._adjust_text_height()
        self.updateGeometry()

    def _update_bubble_geometry(self):
        if getattr(self, "_is_resizing_geometry", False):
            return
        self._is_resizing_geometry = True
        try:
            if self.bubble_container is None:
                return
            
            # АРХИТЕКТУРНОЕ ИСПРАВЛЕНИЕ: 100% разрыв петли обратной связи.
            # Ищем строго жесткое окно QScrollArea через isinstance.
            safe_parent_w = 1000
            p = self.parentWidget()
            from PyQt6.QtWidgets import QScrollArea
            while p:
                if isinstance(p, QScrollArea):
                    safe_parent_w = p.width()
                    break
                p = p.parentWidget()

            if safe_parent_w <= 100:
                return

            # Квантование ширины родителя с шагом не менее 16px
            safe_parent_w = (safe_parent_w // 16) * 16

            text = self.translated_text if (self.is_showing_translation and self.translated_text) else (self.raw_text or "")
            
            line_count = len(text.splitlines()) if text else 1
            char_count = len(text)
            has_code_or_art = bool(self.image_path or self.file_paths or "```" in text or "<pre" in text)

            # Ратчет (храповик) контента
            if not hasattr(self, "_peak_char_count"):
                self._peak_char_count = char_count
            if char_count > self._peak_char_count:
                self._peak_char_count = char_count
                
            if not hasattr(self, "_peak_line_count"):
                self._peak_line_count = line_count
            if line_count > self._peak_line_count:
                self._peak_line_count = line_count

            eval_chars = max(char_count, self._peak_char_count)
            eval_lines = max(line_count, self._peak_line_count)

            if has_code_or_art or eval_lines >= 40 or eval_chars > 1200:
                active_tier = 4
                user_ratio, asst_ratio = 0.92, 0.98
            elif eval_lines >= 20 or eval_chars > 500:
                active_tier = 3
                user_ratio, asst_ratio = 0.82, 0.88
            elif eval_lines >= 8 or eval_chars > 180:
                active_tier = 2
                user_ratio, asst_ratio = 0.70, 0.78
            else:
                active_tier = 1
                user_ratio, asst_ratio = 0.55, 0.65

            # Учет системных элементов строки:
            ROW_UI_OVERHEAD = 120
            RIGHT_MARGIN_4CM = 152
            available_net_w = max(260, safe_parent_w - ROW_UI_OVERHEAD - RIGHT_MARGIN_4CM)

            if self.is_user:
                max_w = min(int(available_net_w * user_ratio), 1000)
            else:
                max_w = min(int(available_net_w * asst_ratio), 1200)

            # Квантование max_w шагом не менее 16px
            max_w = (max_w // 16) * 16

            # ЖЕСТКИЙ БЛОК (Solid Block Architecture)
            if active_tier >= 2:
                target_min = max_w
            else:
                current_w = max(self.bubble_container.width(), self.bubble_container.sizeHint().width())
                if not hasattr(self, "_peak_actual_w"):
                    self._peak_actual_w = current_w

                if self._peak_actual_w > max_w:
                    self._peak_actual_w = max_w

                if current_w > self._peak_actual_w:
                    self._peak_actual_w = current_w
                
                target_min = self._peak_actual_w

            target_min = min(target_min, max_w)
            target_min = (target_min // 16) * 16

            if self.bubble_container.minimumWidth() != target_min or self.bubble_container.maximumWidth() != max_w:
                self.bubble_container.setMinimumWidth(target_min)
                self.bubble_container.setMaximumWidth(max_w)
        finally:
            self._is_resizing_geometry = False

    def _adjust_text_height(self):
        if getattr(self, "_is_resizing_geometry", False):
            return
        self._is_resizing_geometry = True
        try:
            if not hasattr(self, "text_browser") or not self.text_browser:
                return

            if not self.raw_text or not self.raw_text.strip():
                self.text_browser.setFixedHeight(0)
                self.text_browser.setVisible(False)
                return
            else:
                self.text_browser.setVisible(True)

            doc = self.text_browser.document()
            doc.setDocumentMargin(6)

            target_w = 0
            if self.bubble_container and self.bubble_container.width() > 100:
                target_w = self.bubble_container.width() - 40
            elif self.width() > 100:
                target_w = int(self.width() * 0.85) - 40
            else:
                target_w = 780

            # Квантование ширины документа шагом не менее 16px
            target_w = (target_w // 16) * 16

            # АРХИТЕКТУРНОЕ ИСПРАВЛЕНИЕ: Абсолютная точность ширины документа
            if target_w > 80:
                doc.setTextWidth(target_w)

            ideal_h = int(doc.documentLayout().documentSize().height())
            final_h = max(ideal_h + 12, 24)
            if abs(self.text_browser.height() - final_h) > 4:
                self.text_browser.setFixedHeight(final_h)
        finally:
            self._is_resizing_geometry = False

    def set_knowledge_sources(self, sources: List[Dict[str, Any]]):
        """
        Устанавливает список использованных источников базы знаний.
        Если sources пуст, HUD-контейнер скрывается без остаточных отступов.
        Если sources содержит элементы, монтируется неоновый HUD-контейнер с микро-чипами.
        """
        self.knowledge_sources = sources or []
        if getattr(self, "knowledge_hud", None) is not None:
            self.knowledge_hud.set_sources(self.knowledge_sources)
            self._update_bubble_geometry()

    def get_or_create_thought_spoiler(self):
        """Возвращает существующий или создает ThoughtSpoilerWidget над текстовым блоком."""
        if getattr(self, "thought_spoiler", None) is None:
            from ui.components.thought_spoiler_widget import ThoughtSpoilerWidget
            self.thought_spoiler = ThoughtSpoilerWidget(self.bubble_container)
            if hasattr(self, "bubble_layout"):
                if getattr(self, "knowledge_hud", None):
                    idx = self.bubble_layout.indexOf(self.knowledge_hud)
                    self.bubble_layout.insertWidget(idx + 1, self.thought_spoiler)
                else:
                    self.bubble_layout.insertWidget(1, self.thought_spoiler)
            self.thought_spoiler.setVisible(False)
            self.thought_spoiler.hide()
        return self.thought_spoiler

    def append_thought_chunk(self, chunk: str):
        """Дописывает чанк токенов в спойлер мыслей и показывает его."""
        spoiler = self.get_or_create_thought_spoiler()
        if not spoiler.isVisible():
            spoiler.setVisible(True)
            spoiler.show()
        spoiler.append_thought_chunk(chunk)
        self._update_bubble_geometry()

    def finalize_thought(self, duration_sec: float = 0.0, token_count: int = 0):
        """Фиксирует метрики мыслей и сворачивает спойлер."""
        if getattr(self, "thought_spoiler", None) is not None:
            self.thought_spoiler.finalize_thought(duration_sec, token_count)
            self._update_bubble_geometry()

    def set_thought_data(self, text: str, duration: float = 0.0, token_count: int = 0):
        """Устанавливает готовый блок мыслей (из истории или после завершения генерации)."""
        clean = str(text or "").strip()
        if not clean:
            if getattr(self, "thought_spoiler", None) is not None:
                self.thought_spoiler.setVisible(False)
                self.thought_spoiler.hide()
                self._update_bubble_geometry()
            return

        spoiler = self.get_or_create_thought_spoiler()
        self.thought_data = {
            "content": clean,
            "duration": float(duration),
            "token_count": int(token_count)
        }
        spoiler.set_thought_data(clean, duration, token_count)
        self._update_bubble_geometry()

    def set_text(self, text: str):
        self.original_text = text
        self.raw_text = text
        self._update_rendered_text()
        self._refresh_layout()
        if getattr(self, "footer_container", None):
            self.footer_container.setVisible(True)

    def update_avatar(self, new_avatar_path: Optional[Path]):
        self.avatar_path = Path(new_avatar_path) if new_avatar_path else None
        if hasattr(self, "avatar_lbl") and self.avatar_lbl:
            self.avatar_lbl.setPixmap(
                render_circular_avatar(
                    avatar_path=self.avatar_path,
                    name=self.sender,
                    size=64,
                    is_user=self.is_user,
                    is_error=self.is_error
                )
            )

    def set_negative_prompt(self, negative_prompt: str):
        self.negative_prompt = negative_prompt
        if not self.generation_params:
            self.generation_params = {}
        self.generation_params["negative_prompt"] = negative_prompt
        self.set_generation_params(self.generation_params)

    def _render_generation_info_accordion(self, target_layout: Optional[QLayout] = None, max_width: Optional[int] = None):
        if not self.generation_params:
            return
        if self.gen_info_widget is not None:
            self.gen_info_widget.deleteLater()
            self.gen_info_widget = None

        accordion = GenerationInfoAccordionWidget(self.generation_params, max_width=max_width, parent=self)
        self.gen_info_widget = accordion
        if target_layout is not None:
            target_layout.addWidget(accordion)
        elif self.bubble_container and self.bubble_container.layout():
            layout = self.bubble_container.layout()
            layout.addWidget(accordion)

    def set_generation_params(self, params: dict):
        self.generation_params = params
        if self.negative_prompt and "negative_prompt" not in self.generation_params:
            self.generation_params["negative_prompt"] = self.negative_prompt

        target_layout = None
        if self.gen_info_widget is not None and self.gen_info_widget.parent():
            target_layout = self.gen_info_widget.parent().layout()
        if not target_layout:
            target_layout = getattr(self, "art_layout", None) or (self.bubble_container.layout() if self.bubble_container else None)
        self._render_generation_info_accordion(target_layout=target_layout, max_width=None)

    def set_translation(self, translated_text: str):
        self.translated_text = translated_text
        self.is_showing_translation = True
        if self.btn_translate:
            self.btn_translate.setText("🌐 Оригинал")
        self._update_rendered_text()
        self._refresh_layout()

    def _toggle_translation(self):
        if not self.translated_text:
            if self.btn_translate:
                self.btn_translate.setText("⏳ Перевод...")
                self.btn_translate.setToolTip("Выполняется перевод сообщения...")
                self._refresh_layout()
            self.translate_requested.emit(self.original_text, self)
            return

        self.is_showing_translation = not self.is_showing_translation
        self._update_rendered_text()
        if self.btn_translate:
            self.btn_translate.setText("🌐 Оригинал" if self.is_showing_translation else "🌐 Перевести")
            self.btn_translate.setToolTip("Показать оригинал" if self.is_showing_translation else "Перевести на русский")
        self._refresh_layout()

    def set_scene_art_state(self, is_ready: bool, cue_text: str = ""):
        """
        Управляет доступностью и внешним видом кнопки арт-сцены:
        - is_ready=False: анализ еще идет, клик заблокирован, текст '⏳ Подготовка сцены...'
        - is_ready=True: сцена готова, кнопка активна, превью кадра в тултипе.
        """
        if not getattr(self, "btn_scene_art", None):
            return

        try:
            self.btn_scene_art.setEnabled(is_ready)
            if not is_ready:
                self.btn_scene_art.setText("⏳ Подготовка сцены...")
                self.btn_scene_art.setStyleSheet("""
                    QPushButton {
                        background: transparent;
                        border: none;
                        color: #64748B;
                        font-size: 11px;
                        padding: 0px 2px;
                    }
                """)
                self.btn_scene_art.setToolTip("Идет фоновый анализ окружения и динамики сцены...")
                
                # АРХИТЕКТУРНОЕ ИСПРАВЛЕНИЕ: Таймер-предохранитель (Failsafe)
                if not hasattr(self, "_scene_art_timer"):
                    from PyQt6.QtCore import QTimer
                    self._scene_art_timer = QTimer(self)
                    self._scene_art_timer.setSingleShot(True)
                    self._scene_art_timer.timeout.connect(lambda: self.set_scene_art_state(True))
                self._scene_art_timer.start(12000)
            else:
                if hasattr(self, "_scene_art_timer"):
                    self._scene_art_timer.stop()
                self.btn_scene_art.setText("🎨 Арт сцены")
                self.btn_scene_art.setStyleSheet("""
                    QPushButton {
                        background: transparent;
                        border: none;
                        color: #C084FC;
                        font-size: 11px;
                        padding: 0px 2px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        color: #00F0FF;
                    }
                """)
                if cue_text:
                    self.btn_scene_art.setToolTip(f"Готовый кадр сцены:\n{cue_text}")
                else:
                    self.btn_scene_art.setToolTip("Создать иллюстрацию сцены через SD WebUI Forge")

            # Форсируем перерасчет макета для предотвращения визуальных артефактов
            self._refresh_layout()
        except RuntimeError:
            pass

    def show_art_loading_placeholder(self):
        """Интерфейсный метод для специализированных баблов (переопределяется в AssistantChatMessageWidget)."""
        pass

    def hide_art_loading_placeholder(self):
        """Интерфейсный метод для специализированных баблов (переопределяется в AssistantChatMessageWidget)."""
        pass

    def set_scene_art_busy(self, busy: bool, text: Optional[str] = None):
        """Детерминированное управление состоянием кнопки генерации кадра."""
        if getattr(self, "btn_scene_art", None):
            try:
                self.btn_scene_art.setEnabled(not busy)
                if busy:
                    self.btn_scene_art.setText(text or "⏳ Генерация...")
                else:
                    self.btn_scene_art.setText("🎨 Арт сцены")
                self._refresh_layout()
            except RuntimeError:
                pass

    def _on_scene_art_click(self, btn: Optional[QPushButton] = None):
        """Запуск генерации кадра сцены с приоритетом готового visual_intent."""
        self.set_scene_art_busy(True, "⏳ Подготовка кадра...")
        self._refresh_layout()
        
        prompt_to_emit = self.original_text
        if self.visual_intent:
            if isinstance(self.visual_intent, dict):
                prompt_to_emit = self.visual_intent.get("prompt") or self.original_text
            elif hasattr(self.visual_intent, "prompt"):
                prompt_to_emit = getattr(self.visual_intent, "prompt", None) or self.original_text
                
        self.scene_art_requested.emit(prompt_to_emit, self)

    def _update_rendered_text(self):
        current = self.translated_text if (self.is_showing_translation and self.translated_text) else self.original_text
        self.raw_text = current

        if not getattr(self, "text_browser", None) and getattr(self, "bubble_container", None):
            self.text_browser = SelectableTextBrowser(self.bubble_container)
            self.text_browser.setReadOnly(True)
            self.text_browser.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            self.text_browser.setOpenExternalLinks(False)
            self.text_browser.setOpenLinks(False)
            self.text_browser.anchorClicked.connect(self._on_anchor_clicked)
            self.text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.text_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.text_browser.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse |
                Qt.TextInteractionFlag.TextSelectableByKeyboard |
                Qt.TextInteractionFlag.LinksAccessibleByMouse |
                Qt.TextInteractionFlag.LinksAccessibleByKeyboard
            )
            self.text_browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.text_browser.customContextMenuRequested.connect(self._show_context_menu)

            font = QFont("Inter", self.font_size)
            font.setFamilies(["Inter", "Segoe UI", "sans-serif"])
            self.text_browser.setFont(font)
            if self.text_browser.document():
                self.text_browser.document().setDefaultFont(font)

            text_color = "#FCA5A5" if self.is_error else ("#FFFFFF" if self.is_user else "#F1F5F9")
            self.text_browser.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: transparent;
                    color: {text_color};
                    border: none;
                    font-family: 'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
                    font-size: {self.font_size}px;
                    line-height: 1.18;
                    selection-background-color: #00F0FF;
                    selection-color: #000000;
                }}
            """)
            if self.bubble_container.layout():
                self.bubble_container.layout().insertWidget(1, self.text_browser)

        if self.text_browser:
            self.text_browser.setHtml(self._markdown_to_html(current))
            self._update_bubble_geometry()
            self._adjust_text_height()

    def set_font_size(self, size: int):
        self.font_size = size
        if hasattr(self, "text_browser") and self.text_browser:
            font = QFont("Inter", size)
            font.setFamilies(["Inter", "Segoe UI", "sans-serif"])
            self.text_browser.setFont(font)
            if self.text_browser.document():
                self.text_browser.document().setDefaultFont(font)
            text_color = "#FCA5A5" if self.is_error else ("#FFFFFF" if self.is_user else "#F1F5F9")
            self.text_browser.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: transparent;
                    color: {text_color};
                    border: none;
                    font-family: 'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
                    font-size: {size}px;
                    line-height: 1.18;
                    selection-background-color: #00F0FF;
                    selection-color: #000000;
                }}
            """)
            self._adjust_text_height()

    def attach_audio(self, audio_path: Path):
        self.audio_path = audio_path
        if not self.audio_widget and audio_path.exists() and self.bubble_container:
            self.audio_widget = AudioPlayerWidget(audio_path, self.bubble_container)
            layout = self.bubble_container.layout()
            layout.addWidget(self.audio_widget)

    def add_art_proposal_chip(self, prompt_text: str, on_confirm_callback):
        if getattr(self, "art_proposal_chip", None) is not None:
            return
        self.art_proposal_chip = QPushButton("🎨 Нарисовать иллюстрацию", self.bubble_container)
        self.art_proposal_chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.art_proposal_chip.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 240, 255, 0.12);
                color: #00F0FF;
                border: 1px solid #00F0FF;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: bold;
                margin-top: 4px;
            }
            QPushButton:hover {
                background-color: rgba(0, 240, 255, 0.25);
                color: #FFFFFF;
            }
        """)
        def _on_click():
            self.art_proposal_chip.setEnabled(False)
            self.art_proposal_chip.setText("⏳ Генерация...")
            self._refresh_layout()
            on_confirm_callback()
            
        self.art_proposal_chip.clicked.connect(_on_click)
        layout = self.bubble_container.layout()
        layout.addWidget(self.art_proposal_chip)

    def set_image(self, img_path: Union[str, Path]):
        p = Path(img_path)
        self.image_path = p
        if hasattr(self, "bubble_container") and self.bubble_container:
            for child in self.bubble_container.findChildren(QWidget, "DynamicImageContainer"):
                try:
                    child.deleteLater()
                except Exception:
                    pass
        if getattr(self, "art_placeholder", None) is not None:
            self.hide_art_loading_placeholder()
        self.attach_file(p)

    def attach_file(self, file_path: Path, generation_params: Optional[dict] = None):
        if not self.bubble_container:
            return
        if getattr(self, "art_placeholder", None) is not None:
            self.hide_art_loading_placeholder()
        if getattr(self, "art_proposal_chip", None):
            self.art_proposal_chip.deleteLater()
            self.art_proposal_chip = None
        if generation_params:
            self.generation_params = generation_params
        elif self.negative_prompt:
            if not self.generation_params:
                self.generation_params = {}
            if "negative_prompt" not in self.generation_params:
                self.generation_params["negative_prompt"] = self.negative_prompt
        ext = file_path.suffix.lower()
        layout = self.bubble_container.layout()
        if ext in ('.png', '.jpg', '.jpeg', '.webp'):
            img_w = self._create_image_thumbnail(file_path, generation_params=generation_params or self.generation_params)
            target_w = img_w
        else:
            card = FileCardWidget(file_path, self.bubble_container)
            target_w = card

        layout.addWidget(target_w)
            
        # АРХИТЕКТУРНОЕ ИСПРАВЛЕНИЕ: Пересчет ширины при вставке медиа
        self._refresh_layout()

    def _create_image_thumbnail(self, image_path: Path, generation_params: Optional[dict] = None) -> QWidget:
        container = QWidget(self.bubble_container)
        container.setObjectName("DynamicImageContainer")
        container.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        container.setStyleSheet("background: transparent; border: none;")
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(0, 4, 0, 4)
        c_layout.setSpacing(4)
        c_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        img_btn = QPushButton(container)
        img_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        img_btn.setToolTip("Клик — полноразмерный просмотр")

        rounded_thumb = get_cached_art_pixmap(image_path, target_size=380, radius=14.0)
        thumb_w = 380
        if not rounded_thumb.isNull():
            thumb_w = rounded_thumb.width()
            img_btn.setIcon(QIcon(rounded_thumb))
            img_btn.setIconSize(rounded_thumb.size())
            img_btn.setFixedSize(rounded_thumb.size())
            container.setMinimumWidth(thumb_w)

        img_btn.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0px; }")

        img_btn.clicked.connect(lambda: ImagePreviewDialog(image_path, self).exec())
        c_layout.addWidget(img_btn)

        eff_params = generation_params or self.generation_params
        if eff_params:
            accordion = GenerationInfoAccordionWidget(eff_params, max_width=None, parent=container)
            c_layout.addWidget(accordion)
            self.gen_info_widget = accordion

        return container

    def _open_image_preview(self, image_path: Path):
        if image_path and image_path.exists():
            dlg = ImagePreviewDialog(image_path, self)
            dlg.exec()

    def _copy_text(self):
        if hasattr(self, "text_browser") and self.text_browser:
            cursor = self.text_browser.textCursor()
            selected = cursor.selectedText()
            if selected:
                cleaned = selected.replace('\u2029', '\n').replace('\u2028', '\n')
                QApplication.clipboard().setText(cleaned)
                return
        raw = getattr(self, "raw_text", "") or ""
        cleaned_raw = raw.replace('\u2029', '\n').replace('\u2028', '\n')
        QApplication.clipboard().setText(cleaned_raw)

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

        selected_text = ""
        if hasattr(self, "text_browser") and self.text_browser:
            cursor = self.text_browser.textCursor()
            selected_text = cursor.selectedText().strip()

        if selected_text:
            act_copy_sel = menu.addAction("📋 Копировать выделенный фрагмент")
            act_copy_sel.triggered.connect(lambda: QApplication.clipboard().setText(selected_text))

        act_copy_all = menu.addAction("📋 Копировать весь текст сообщения")
        act_copy_all.triggered.connect(lambda: QApplication.clipboard().setText(self.raw_text))

        menu.addSeparator()
        if not self.is_error:
            trans_act_title = "🌐 Показать оригинал" if (self.is_showing_translation and self.translated_text) else "🌐 Перевести"
            act_trans = menu.addAction(trans_act_title)
            act_trans.triggered.connect(self._toggle_translation)

        if not self.is_user and not self.is_error:
            if getattr(self, "is_roleplay", False):
                act_art = menu.addAction("🎨 Иллюстрация сцены")
                act_art.triggered.connect(lambda: self._on_scene_art_click())

            act_tts = menu.addAction("🔊 Озвучить через F5-TTS")
            act_tts.triggered.connect(lambda: self.tts_requested.emit(self.raw_text))

            act_regen = menu.addAction("🔄 Регенерировать ответ")
            act_regen.triggered.connect(lambda: self.regenerate_requested.emit(self))

        if self.image_path and self.image_path.exists():
            menu.addSeparator()
            act_open_full = menu.addAction("🖼️ Открыть оригинал в Windows (100% Full-res)")
            act_open_full.triggered.connect(lambda: os.startfile(str(self.image_path)))

            act_show_file = menu.addAction("📁 Показать файл в проводнике")
            act_show_file.triggered.connect(lambda: subprocess.Popen(f'explorer /select,"{self.image_path}"'))

        menu.addSeparator()
        act_del = menu.addAction("🗑️ Удалить сообщение")
        act_del.triggered.connect(lambda: self.delete_requested.emit(self))

        menu.exec(QCursor.pos())

    def _on_anchor_clicked(self, qurl: QUrl):
        raw_url = qurl.toString().strip()
        if raw_url.lower().startswith(("http://", "https://")):
            QDesktopServices.openUrl(qurl)
            return

        # Очистка служебных префиксов (run: и open: исключены в целях безопасности)
        clean_path = raw_url
        for pref in ("file:///", "file://", "file:"):
            if clean_path.lower().startswith(pref):
                clean_path = clean_path[len(pref):]
                break

        clean_path = clean_path.replace("/", "\\").strip('"\'')
        target = Path(clean_path)
        if not target.is_absolute():
            target = WORKSPACE_DIR / target.name

        DANGEROUS_EXTS = {".exe", ".bat", ".cmd", ".vbs", ".ps1", ".msi", ".dll"}
        if target.suffix.lower() in DANGEROUS_EXTS:
            logger.warning(f"[ChatBubble] Запуск исполняемого файла заблокирован из соображений безопасности: {target}")
            return

        try:
            if target.exists():
                norm = os.path.normpath(str(target.resolve()))
                if sys.platform == "win32":
                    import ctypes
                    if target.is_file():
                        ctypes.windll.shell32.ShellExecuteW(None, "open", "explorer.exe", f'/select,"{norm}"', None, 1)
                    else:
                        ctypes.windll.shell32.ShellExecuteW(None, "open", norm, None, None, 1)
                else:
                    subprocess.Popen(["xdg-open", norm], shell=False)
            elif WORKSPACE_DIR.exists() and "workspace" in clean_path.lower():
                norm = os.path.normpath(str(WORKSPACE_DIR.resolve()))
                if sys.platform == "win32":
                    import ctypes
                    ctypes.windll.shell32.ShellExecuteW(None, "open", norm, None, None, 1)
                else:
                    subprocess.Popen(["xdg-open", norm], shell=False)
            else:
                QDesktopServices.openUrl(qurl)
        except Exception as e:
            logger.error(f"[ChatBubble] Ошибка открытия ссылки/файла '{raw_url}': {e}")

    def add_workspace_file_card(self, file_path: Path | str, on_run: Optional[callable] = None) -> WorkspaceFileCardWidget:
        p = Path(file_path).resolve()
        for child in self.findChildren(WorkspaceFileCardWidget):
            try:
                if child.file_path.resolve() == p:
                    return child
            except Exception:
                pass

        card = WorkspaceFileCardWidget(file_path, on_run=None, parent=self.bubble_container)
        if on_run:
            card.run_script_requested.connect(on_run)
        else:
            card.run_script_requested.connect(self.run_script_requested.emit)
        if self.bubble_container and self.bubble_container.layout():
            layout = self.bubble_container.layout()
            layout.addWidget(card)
        return card

    def add_console_spoiler(
        self,
        title: str = "Консоль вывода",
        output: str = "",
        is_error: bool = False,
        return_code: int = 0
    ) -> ConsoleOutputSpoilerWidget:
        for child in self.findChildren(ConsoleOutputSpoilerWidget):
            if getattr(child, "title", None) == title:
                child.output = output.strip() if output else "(нет вывода)"
                child.is_error = is_error or (return_code != 0)
                child.return_code = return_code
                if hasattr(child, "console_browser"):
                    child.console_browser.setPlainText(child.output)
                child._update_header_text()
                return child

        spoiler = ConsoleOutputSpoilerWidget(
            title=title,
            output=output,
            is_error=is_error,
            return_code=return_code,
            parent=self.bubble_container
        )
        if self.bubble_container and self.bubble_container.layout():
            layout = self.bubble_container.layout()
            layout.addWidget(spoiler)
        return spoiler

    def _markdown_to_html(self, text: str) -> str:
        if not text:
            return ""
        normalized_text = text.replace(chr(8232), '\n').replace(chr(8233), '\n').replace('\r\n', '\n')
        escaped = html.escape(normalized_text)

        def code_block_sub(match):
            code = match.group(1)
            return f"<pre style='background-color:#080B11; border:1px solid #1E273A; border-radius:6px; padding:8px; color:#00F0FF; font-family:Consolas, monospace; white-space:pre-wrap; word-break:break-word;'>{code}</pre>"
        escaped = re.sub(r'```(?:[a-zA-Z0-9_-]+)?\n(.*?)```', code_block_sub, escaped, flags=re.DOTALL)

        escaped = re.sub(r'`([^`]+)`', r"<code style='background-color:#141C2B; color:#38BDF8; padding:1px 4px; border-radius:4px; font-family:Consolas;'>\1</code>", escaped)

        def path_link_sub(match):
            raw_path = match.group(1)
            raw_path = re.sub(r'(&(?:#x27|#39|quot|amp|lt|gt);|[,.;:!?\"\'])*$', '', raw_path)
            norm_path = raw_path.replace('\\', '/')
            return f'<a href="file:///{norm_path}" style="color:#00F0FF; text-decoration:none;" title="Открыть в проводнике">{raw_path}</a>'

        escaped = re.sub(r'(?<!href=[\"\']file:///)((?:[a-zA-Z]:[\\/]|(?:workspace[\\/]))[^\s<>\[\]\"\'\)\(&;]+)', path_link_sub, escaped)

        escaped = re.sub(r'\*\*(.*?)\*\*', r"<strong>\1</strong>", escaped)
        escaped = re.sub(r'\*([^*]+)\*', r"<em style='color:#A78BFA;'>*\1*</em>", escaped)

        escaped = re.sub(r'^### (.*)$', r"<h3 style='color:#38BDF8; margin-top:4px; margin-bottom:2px;'>\1</h3>", escaped, flags=re.MULTILINE)
        escaped = re.sub(r'^## (.*)$', r"<h2 style='color:#00F0FF; margin-top:5px; margin-bottom:2px;'>\1</h2>", escaped, flags=re.MULTILINE)
        escaped = re.sub(r'^# (.*)$', r"<h1 style='color:#F8FAFC; margin-top:6px; margin-bottom:3px;'>\1</h1>", escaped, flags=re.MULTILINE)

        escaped = escaped.replace('\n', '<br>')
        return f"""<div style="font-family: 'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif; line-height: 1.2; letter-spacing: -0.01em; overflow-wrap: break-word; word-break: break-word; margin: 0; padding: 0;">{escaped}</div>"""

    def update_font_size(self, font_size: int):
        self.set_font_size(font_size)


def __getattr__(name: str):
    if name == "CyberpunkArtPlaceholderWidget":
        from ui.components.assistant_chat_bubble import CyberpunkArtPlaceholderWidget
        return CyberpunkArtPlaceholderWidget
    if name == "RPChatMessageWidget":
        from ui.components.rp.rp_chat_bubble import RPChatMessageWidget
        return RPChatMessageWidget
    if name == "AssistantChatMessageWidget":
        from ui.components.assistant_chat_bubble import AssistantChatMessageWidget
        return AssistantChatMessageWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")