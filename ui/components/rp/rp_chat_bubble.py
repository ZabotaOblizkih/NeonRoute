"""
Roleplay Chat Message Bubble Component for NeonRoute.
Specialized bubble for Roleplay mode with status HUD, thought spoiler, and interactive choices.
"""

import html
import re
import logging
from pathlib import Path
from typing import Optional, List, Union, Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton

from ui.components.chat_bubble import ChatMessageWidget, SelectableTextBrowser
from ui.components.rpg_elements import (
    RPGBundle,
    RPGContentParser,
    RPGStatusWidget,
    RPGThoughtWidget,
    RPGChoicesWidget
)

logger = logging.getLogger("NeonRoute.RPChatBubble")

FILE_PATH_RE = re.compile(
    r'\b([a-zA-Z]:[/\\\\][a-zA-Z0-9_./\\\\-]+|workspace[/\\\\][a-zA-Z0-9_./\\\\-]+)'
)


class RPChatMessageWidget(ChatMessageWidget):
    """
    Специализированный бабл сообщений для ролевого режима (Tavern/RP).
    Инкапсулирует парсинг тегов <thought>, <stats>, <choices>, <meters>
    и интерактивные ролевые виджеты.
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
        is_roleplay: bool = True,
        generation_params: Optional[dict] = None,
        visual_intent: Optional[dict] = None,
        message_id: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        parent=None,
        **kwargs
    ):
        self.visual_intent: Optional[dict] = visual_intent
        self.art_source: Optional[str] = None
        self.btn_scene_art: Optional[QPushButton] = None
        self._pending_art_trigger = False
        self.rpg_status_widget: Optional[RPGStatusWidget] = None
        self.rpg_thought_widget: Optional[RPGThoughtWidget] = None
        self.rpg_choices_widget: Optional[RPGChoicesWidget] = None

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
            is_roleplay=True,
            generation_params=generation_params,
            message_id=message_id,
            reply_to_id=reply_to_id,
            parent=parent,
            **kwargs
        )
        if visual_intent is not None:
            self.visual_intent = visual_intent
        self._setup_rp_widgets()

    def _markdown_to_html(self, text: str) -> str:
        if not text:
            return ""
        normalized_text = text.replace(chr(8232), '\n').replace(chr(8233), '\n').replace('\r\n', '\n')
        escaped = html.escape(normalized_text)
        escaped = RPGContentParser.format_inline_rpg_blocks(escaped)

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

        escaped = FILE_PATH_RE.sub(path_link_sub, escaped)

        escaped = re.sub(r'\*\*(.*?)\*\*', r"<strong>\1</strong>", escaped)
        escaped = re.sub(r'\*([^*]+)\*', r"<em style='color:#A78BFA;'>*\1*</em>", escaped)

        escaped = re.sub(r'^### (.*)$', r"<h3 style='color:#38BDF8; margin-top:4px; margin-bottom:2px;'>\1</h3>", escaped, flags=re.MULTILINE)
        escaped = re.sub(r'^## (.*)$', r"<h2 style='color:#00F0FF; margin-top:5px; margin-bottom:2px;'>\1</h2>", escaped, flags=re.MULTILINE)
        escaped = re.sub(r'^# (.*)$', r"<h1 style='color:#F8FAFC; margin-top:6px; margin-bottom:3px;'>\1</h1>", escaped, flags=re.MULTILINE)

        escaped = escaped.replace('\n', '<br>')
        return f"""<div style="font-family: 'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif; line-height: 1.2; letter-spacing: -0.01em; overflow-wrap: break-word; word-break: break-word; margin: 0; padding: 0;">{escaped}</div>"""

    def _setup_rp_widgets(self):
        """Парсит и монтирует начальные RPG-виджеты при инициализации бабла."""
        if not self.bubble_container:
            return
        initial_text = self.translated_text if (self.is_showing_translation and self.translated_text) else self.raw_text
        if not initial_text:
            return

        bundle = RPGContentParser.parse(initial_text)
        bubble_layout = self.bubble_container.layout()
        if not bubble_layout:
            return

        # 1. Thought Spoiler
        if bundle.thought and not self.rpg_thought_widget:
            self.rpg_thought_widget = RPGThoughtWidget(bundle.thought, self.bubble_container)
            bubble_layout.insertWidget(1, self.rpg_thought_widget)

        # Обновляем основной текст чистым текстом без тегов
        if self.text_browser and bundle.clean_text != initial_text:
            self.text_browser.setHtml(self._markdown_to_html(bundle.clean_text))
            self._adjust_text_height()

        # 2. RPG Status HUD & Meters
        if (bundle.stats or bundle.meters) and not self.rpg_status_widget:
            self.rpg_status_widget = RPGStatusWidget(bundle.stats or {}, bundle.meters or {}, self.bubble_container)
            target_idx = bubble_layout.count() - 1 if not self.rpg_choices_widget else bubble_layout.indexOf(self.rpg_choices_widget)
            bubble_layout.insertWidget(max(1, target_idx), self.rpg_status_widget)

        # 3. Interactive Choices Buttons
        if bundle.choices and not self.rpg_choices_widget:
            self.rpg_choices_widget = RPGChoicesWidget(bundle.choices, self.bubble_container)
            self.rpg_choices_widget.choice_selected.connect(self.choice_selected.emit)
            bubble_layout.insertWidget(bubble_layout.count() - 1, self.rpg_choices_widget)

    def _update_rendered_text(self):
        current = self.translated_text if (self.is_showing_translation and self.translated_text) else self.original_text
        self.raw_text = current
        bundle = RPGContentParser.parse(current)

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
            self.text_browser.setHtml(self._markdown_to_html(bundle.clean_text))
            self._update_bubble_geometry()
            self._adjust_text_height()

        layout = self.bubble_container.layout() if self.bubble_container else None

        # Manage Thoughts
        if bundle.thought:
            if not self.rpg_thought_widget:
                self.rpg_thought_widget = RPGThoughtWidget(bundle.thought, self.bubble_container)
                if layout:
                    layout.insertWidget(1, self.rpg_thought_widget)
        elif self.rpg_thought_widget:
            self.rpg_thought_widget.deleteLater()
            self.rpg_thought_widget = None

        # Manage Status & Meters
        if bundle.stats or bundle.meters:
            if not self.rpg_status_widget:
                self.rpg_status_widget = RPGStatusWidget(bundle.stats or {}, bundle.meters or {}, self.bubble_container)
                if layout:
                    target_idx = layout.count() - 1 if not self.rpg_choices_widget else layout.indexOf(self.rpg_choices_widget)
                    layout.insertWidget(max(1, target_idx), self.rpg_status_widget)
            else:
                self.rpg_status_widget.update_data(bundle.stats or {}, bundle.meters or {})
        elif self.rpg_status_widget:
            self.rpg_status_widget.deleteLater()
            self.rpg_status_widget = None

        # Manage Choices
        if bundle.choices:
            if not self.rpg_choices_widget:
                self.rpg_choices_widget = RPGChoicesWidget(bundle.choices, self.bubble_container)
                self.rpg_choices_widget.choice_selected.connect(self.choice_selected.emit)
                if layout:
                    layout.insertWidget(layout.count() - 1, self.rpg_choices_widget)
        elif self.rpg_choices_widget:
            self.rpg_choices_widget.deleteLater()
            self.rpg_choices_widget = None
