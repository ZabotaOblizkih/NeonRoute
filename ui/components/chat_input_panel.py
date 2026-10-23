"""
Chat Input Panel Component for NeonRoute.
Encapsulates the bottom input capsule, attachment chips, action buttons
(Attach, Mic, Emoji, Engine Mode), and ChatInputBox.
"""

from pathlib import Path
from typing import List, TYPE_CHECKING
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QColor
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QWidget, QGraphicsDropShadowEffect
)
from ui.components.input_box import ChatInputBox

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ChatInputPanel(QFrame):
    def __init__(self, window: 'MainWindow'):
        super().__init__(window)
        self.window = window
        self.attached_files: List[Path] = []
        self.setObjectName("InputAreaContainer")
        self._setup_style()
        self._setup_ui()

    def _setup_style(self):
        self.setStyleSheet("""
            QFrame#InputAreaContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(16, 22, 36, 0.62), stop:1 rgba(10, 14, 22, 0.68));
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                margin: 0px 14px 12px 14px;
            }
            QPushButton#AttachButton, QPushButton#MicButton, QPushButton#EmojiButton {
                background-color: rgba(8, 12, 20, 0.70);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                color: #94A3B8;
                font-size: 15px;
                padding: 0px;
            }
            QPushButton#AttachButton:hover, QPushButton#MicButton:hover, QPushButton#EmojiButton:hover {
                background-color: rgba(0, 240, 255, 0.15);
                border: 1px solid rgba(0, 240, 255, 0.40);
                color: #00F0FF;
            }
            QPushButton#AttachButton:pressed, QPushButton#MicButton:pressed, QPushButton#EmojiButton:pressed {
                background-color: rgba(0, 240, 255, 0.28);
                border: 1px solid #00F0FF;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setYOffset(6)
        shadow.setXOffset(0)
        shadow.setColor(QColor(0, 0, 0, 200))
        self.setGraphicsEffect(shadow)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # 1. Attachments Chips Container
        self.attachments_container = QWidget(self)
        self.attachments_layout = QHBoxLayout(self.attachments_container)
        self.attachments_layout.setContentsMargins(0, 0, 0, 0)
        self.attachments_layout.setSpacing(6)
        self.attachments_container.setVisible(False)
        layout.addWidget(self.attachments_container)

        # 2. Main Input Row
        edit_row = QHBoxLayout()
        edit_row.setSpacing(6)
        edit_row.setContentsMargins(0, 0, 0, 0)

        self.btn_attach = QPushButton("📎", self)
        self.btn_attach.setObjectName("AttachButton")
        self.btn_attach.setToolTip("Прикрепить файл или изображение")
        self.btn_attach.setFixedSize(32, 32)
        self.btn_attach.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_attach.clicked.connect(self.window._select_attachment)
        edit_row.addWidget(self.btn_attach, alignment=Qt.AlignmentFlag.AlignBottom)

        self.btn_mic = QPushButton("🎙️", self)
        self.btn_mic.setObjectName("MicButton")
        self.btn_mic.setToolTip("Голосовой ввод (Faster-Whisper)")
        self.btn_mic.setFixedSize(32, 32)
        self.btn_mic.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_mic.clicked.connect(self.window._toggle_voice_input)
        edit_row.addWidget(self.btn_mic, alignment=Qt.AlignmentFlag.AlignBottom)

        self.btn_emoji = QPushButton("😀", self)
        self.btn_emoji.setObjectName("EmojiButton")
        self.btn_emoji.setToolTip("Вставить эмодзи")
        self.btn_emoji.setFixedSize(32, 32)
        self.btn_emoji.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_emoji.clicked.connect(self.window._show_emoji_picker)
        edit_row.addWidget(self.btn_emoji, alignment=Qt.AlignmentFlag.AlignBottom)


        self.input_box = ChatInputBox(self)
        self.input_box.send_requested.connect(self.window._on_send_clicked)
        self.input_box.stop_requested.connect(self.window._on_stop_clicked)
        self.input_box.image_pasted.connect(self.window._on_image_pasted)
        self.input_box.files_dropped.connect(self.window._on_files_dropped)
        edit_row.addWidget(self.input_box, stretch=1)

        layout.addLayout(edit_row)

    # --- Attachment Management ---
    def add_attachment(self, file_path: Path):
        if file_path not in self.attached_files:
            self.attached_files.append(file_path)
            self.update_attachments_ui()

    def remove_attachment(self, file_path: Path):
        if file_path in self.attached_files:
            self.attached_files.remove(file_path)
            self.update_attachments_ui()

    def clear_attachments(self):
        self.attached_files.clear()
        self.update_attachments_ui()

    def update_attachments_ui(self):
        while self.attachments_layout.count():
            item = self.attachments_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.attached_files:
            self.attachments_container.setVisible(False)
            self.input_box.set_has_attachments(False)
            return

        self.input_box.set_has_attachments(True)
        self.attachments_container.setVisible(True)

        for f in self.attached_files:
            chip = QFrame(self.attachments_container)
            chip.setStyleSheet(
                "background-color: #1A2234; border: 1px solid #00F0FF; border-radius: 6px; padding: 2px 6px;"
            )
            c_layout = QHBoxLayout(chip)
            c_layout.setContentsMargins(4, 2, 4, 2)
            c_layout.setSpacing(4)

            display_text = f.name[:15] + "..." if len(f.name) > 15 else f.name
            lbl = QLabel(display_text, chip)
            lbl.setStyleSheet("color: #E2E8F0; font-size: 11px;")
            c_layout.addWidget(lbl)

            rm_btn = QPushButton("✕", chip)
            rm_btn.setStyleSheet("background: transparent; border: none; color: #FF007F; font-weight: bold;")
            rm_btn.clicked.connect(lambda checked, target=f: self.remove_attachment(target))
            c_layout.addWidget(rm_btn)

            self.attachments_layout.addWidget(chip)

        self.attachments_layout.addStretch()
