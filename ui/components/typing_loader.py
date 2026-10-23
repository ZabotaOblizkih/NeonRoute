"""
Typing Loader Indicator for NeonRoute
Displays a pulsing cyberpunk bubble while LLM, Translation, or Forge tasks are executing.
Adheres to Telegram/WhatsApp style with circular avatar and dynamic dancing dots.
"""

import re
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QWidget, QFrame, QHBoxLayout, QLabel

from ui.components.chat_bubble import render_circular_avatar


class TypingLoaderWidget(QWidget):
    """
    Compact animated typing loader bubble in Telegram/WhatsApp style.
    Displays circular avatar of the sender on the left, a glassmorphic bubble with
    clean status text, and dynamically animated dancing dots.
    """
    def __init__(
        self,
        sender_name: str = "NeonRoute",
        action_text: str = "печатает",
        avatar_path: Optional[Path | str] = None,
        icon: Optional[str] = None,
        parent=None
    ):
        super().__init__(parent)
        self.sender_name = sender_name
        # Clean action text of any static trailing dots and leading duplicate emojis
        clean_action = re.sub(r'[\.\s]+$', '', action_text).strip()
        self.action_text = clean_action
        self.avatar_path = Path(avatar_path) if avatar_path else None
        self.dot_count = 1

        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # 1. Circular Avatar (36x36)
        self.avatar_lbl = QLabel(self)
        self.avatar_lbl.setFixedSize(36, 36)
        self.avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_lbl.setStyleSheet("background: transparent;")
        self.avatar_lbl.setPixmap(
            render_circular_avatar(
                avatar_path=self.avatar_path,
                name=self.sender_name,
                size=36,
                is_user=False
            )
        )
        layout.addWidget(self.avatar_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        # 2. Glassmorphic Bubble
        self.bubble = QFrame(self)
        self.bubble.setObjectName("TypingLoaderBubble")
        self.bubble.setStyleSheet("""
            #TypingLoaderBubble {
                background-color: rgba(28, 30, 38, 0.82);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 16px;
                border-top-left-radius: 4px;
                padding: 8px 14px;
            }
        """)

        bubble_layout = QHBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(4, 2, 4, 2)
        bubble_layout.setSpacing(4)

        self.lbl_text = QLabel(self.action_text, self.bubble)
        self.lbl_text.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: 500; background: transparent;")
        bubble_layout.addWidget(self.lbl_text)

        self.lbl_dots = QLabel(".", self.bubble)
        self.lbl_dots.setFixedWidth(24)
        self.lbl_dots.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold; background: transparent;")
        bubble_layout.addWidget(self.lbl_dots)

        layout.addWidget(self.bubble, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        # Dynamic Animation timer (every 350 ms: . -> .. -> ... -> "")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate_tick)
        self.timer.start(350)

    def _animate_tick(self):
        self.dot_count = (self.dot_count + 1) % 4
        self.lbl_dots.setText("." * self.dot_count)

    def stop_animation(self):
        """Stops the animation timer cleanly."""
        self.cleanup()

    def cleanup(self):
        """Stops the animation timer cleanly."""
        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()
