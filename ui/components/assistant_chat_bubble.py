"""
Assistant Chat Message Bubble Component for NeonRoute.
Specialized bubble for Elaria with interactive neon art proposal chips.
Isolated from roleplay mechanics and widgets.
"""

import logging
from pathlib import Path
from typing import Optional, List, Union, Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QWidget, QPushButton
from qfluentwidgets import ToolTipFilter, ToolTipPosition

from ui.components.chat_bubble import ChatMessageWidget, CyberpunkArtPlaceholderWidget

logger = logging.getLogger("NeonRoute.AssistantChatBubble")


class AssistantChatMessageWidget(ChatMessageWidget):
    """
    Специализированный бабл сообщений Эларии.
    Изолирует логику интерактивных неоновых арт-чипов от базового и ролевого чатов.
    """
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
        kwargs.pop("is_roleplay", None)
        super().__init__(
            sender=sender,
            text=text,
            is_user=is_user,
            is_error=is_error,
            image_path=image_path,
            file_paths=file_paths,
            audio_path=audio_path,
            avatar_path=avatar_path,
            translated_text=translated_text,
            is_showing_translation=is_showing_translation,
            font_size=font_size,
            negative_prompt=negative_prompt,
            timestamp=timestamp,
            is_roleplay=False,
            generation_params=generation_params,
            message_id=message_id,
            reply_to_id=reply_to_id,
            visual_intent=visual_intent,
            parent=parent,
            **kwargs
        )
        self.art_proposal_chip: Optional[QPushButton] = None

    def add_art_proposal_chip(self, prompt: str, on_generate: Optional[callable] = None):
        """Монтирует интерактивный неоновый чип генерации под текстом ответа."""
        if not self.bubble_container:
            return

        if getattr(self, "art_proposal_chip", None) is not None:
            try:
                self.art_proposal_chip.deleteLater()
            except Exception:
                pass
            self.art_proposal_chip = None

        chip_btn = QPushButton("🎨 Сгенерировать иллюстрацию", self.bubble_container)
        chip_btn.setObjectName("ArtProposalChip")
        chip_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        chip_btn.setToolTip(f"Запустить генерацию кадра в Forge:\n{prompt}")
        chip_btn.installEventFilter(ToolTipFilter(chip_btn, 300, ToolTipPosition.BOTTOM))
        chip_btn.setStyleSheet("""
            QPushButton#ArtProposalChip {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0, 240, 255, 0.12),
                    stop:1 rgba(14, 165, 233, 0.18));
                color: #00F0FF;
                border: 1px solid rgba(0, 240, 255, 0.45);
                border-radius: 14px;
                padding: 6px 16px;
                font-family: 'Inter', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
                margin-top: 4px;
                margin-bottom: 3px;
                text-align: left;
            }
            QPushButton#ArtProposalChip:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0, 240, 255, 0.28),
                    stop:1 rgba(14, 165, 233, 0.36));
                border-color: #00F0FF;
                color: #FFFFFF;
            }
            QPushButton#ArtProposalChip:pressed {
                background-color: rgba(0, 240, 255, 0.08);
            }
            QPushButton#ArtProposalChip:disabled {
                background-color: rgba(30, 41, 59, 0.5);
                border-color: rgba(148, 163, 184, 0.25);
                color: #94A3B8;
            }
        """)

        def _safe_generate():
            chip_btn.setEnabled(False)
            chip_btn.setText("⏳ Запуск генерации...")
            if on_generate:
                try:
                    on_generate()
                except Exception as ex:
                    logger.error(f"Art generation invocation failed: {ex}")
                    chip_btn.setEnabled(True)
                    chip_btn.setText("🎨 Сгенерировать иллюстрацию")

        chip_btn.clicked.connect(_safe_generate)

        self.art_proposal_chip = chip_btn

        layout = self.bubble_container.layout()
        if layout:
            if hasattr(self, "footer_layout") and self.footer_layout:
                idx = layout.indexOf(self.footer_layout)
                if idx >= 0:
                    layout.insertWidget(idx, chip_btn)
                elif layout.count() > 1:
                    layout.insertWidget(layout.count() - 1, chip_btn)
                else:
                    layout.addWidget(chip_btn)
            elif layout.count() > 1:
                layout.insertWidget(layout.count() - 1, chip_btn)
            else:
                layout.addWidget(chip_btn)

        # Мгновенная фиксация комфортной ширины бабла под кнопку (защита от схлопывания)
        self._update_bubble_geometry()
        self._adjust_text_height()

    def set_scene_art_busy(self, busy: bool, text: Optional[str] = None):
        """Переключает чип Эларии в состояние ожидания при рендере."""
        chip = getattr(self, "art_proposal_chip", None)
        if chip is not None:
            try:
                chip.setEnabled(not busy)
                if busy:
                    chip.setText(text or "⏳ Генерация иллюстрации...")
                else:
                    chip.setText("🎨 Сгенерировать иллюстрацию")
            except RuntimeError:
                pass
        if busy:
            self.show_art_loading_placeholder()
        else:
            self.hide_art_loading_placeholder()
        self._update_bubble_geometry()
