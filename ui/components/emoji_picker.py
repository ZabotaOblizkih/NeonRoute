"""
Emoji Picker Menu Component for NeonRoute.
Provides category tabs and grid selection for expressive chat input.
"""

from typing import List, Tuple
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QMenu, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGridLayout, QWidgetAction
)


class EmojiPickerMenu(QMenu):
    CATEGORIES: List[Tuple[str, List[str]]] = [
        ("Эмоции & Реакции", [
            "😀", "😂", "😊", "🥺", "😳", "😡", "🤔", "🤫", "😍", "🤣", "😭", "😴",
            "👍", "👎", "❤️", "💔", "🖤", "🔥", "💯", "👏", "🎉", "⚡", "⚠️", "✨"
        ]),
        ("Атмосфера & Ролеплей", [
            "👑", "🍷", "⚔️", "💀", "🌙", "🗡️", "🥀", "🕯️", "🔮", "🩸", "⛓️", "🗝️",
            "🎭", "🐺", "🐱", "🦊", "🌹", "🏹", "🛡️", "💎", "💍", "💋", "🌸", "☕"
        ])
    ]

    def __init__(self, target_input, parent=None):
        super().__init__(parent)
        self.target_input = target_input
        self.setObjectName("EmojiPickerMenu")
        self.setStyleSheet("""
            QMenu#EmojiPickerMenu {
                background-color: rgba(13, 18, 30, 0.96);
                border: 1px solid rgba(0, 240, 255, 0.4);
                border-radius: 10px;
                padding: 6px;
            }
        """)
        self._setup_ui()

    def _setup_ui(self):
        container = QWidget(self)
        container.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(6, 6, 6, 6)
        c_layout.setSpacing(6)

        for cat_name, emojis in self.CATEGORIES:
            cat_lbl = QLabel(cat_name, container)
            cat_lbl.setStyleSheet("color: #38BDF8; font-size: 10px; font-weight: bold; margin-top: 2px;")
            c_layout.addWidget(cat_lbl)

            grid_w = QWidget(container)
            grid = QGridLayout(grid_w)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(2)

            cols = 8
            for idx, emoji_char in enumerate(emojis):
                row = idx // cols
                col = idx % cols
                btn = QPushButton(emoji_char, grid_w)
                btn.setFixedSize(28, 28)
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        border: none;
                        font-size: 14px;
                        border-radius: 4px;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        background-color: rgba(0, 240, 255, 0.2);
                    }
                """)
                btn.clicked.connect(lambda _, em=emoji_char: self._on_emoji_clicked(em))
                grid.addWidget(btn, row, col)

            c_layout.addWidget(grid_w)

        action = QWidgetAction(self)
        action.setDefaultWidget(container)
        self.addAction(action)
        self._container = container

    def _on_emoji_clicked(self, emoji_char: str):
        if self.target_input:
            self.target_input.insertPlainText(emoji_char)
            self.target_input.setFocus()
        self.close()

    def show_above_widget(self, anchor_widget: QWidget):
        btn_pos = anchor_widget.mapToGlobal(QPoint(0, 0))
        menu_height = self._container.sizeHint().height() + 16
        pos = QPoint(btn_pos.x(), max(10, btn_pos.y() - menu_height))
        self.exec(pos)
