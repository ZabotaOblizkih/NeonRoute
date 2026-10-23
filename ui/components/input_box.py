"""
Cyberpunk / Glassmorphic Multi-line Input Box for NeonRoute
Features:
- Integrated circular transformer button (32x32 px): Send (➤) ⇄ Stop (⏹) without borders
- Visual Mode Indicator: Button turns Magenta/Purple when Art/Image prompt is detected (disabled in Engineer mode)
- Viewport right padding (42px) to prevent text overlap with action button
- Dynamic smooth height expansion (42px base up to 250px)
- Enter to send, Shift+Enter for newline
- Direct clipboard image capture (Ctrl+V)
- Drag and drop file support
- Guaranteed translucent glass styling
"""

import time
import re
from pathlib import Path
from typing import Optional, List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QImage, QCursor, QColor, QPalette
from PyQt6.QtWidgets import QTextEdit, QApplication, QPushButton

from config import OUTPUTS_DIR


class ChatInputBox(QTextEdit):
    """
    Modern glassmorphic multi-line chat input with dynamic mode highlighting,
    auto-expansion up to 250px, and integrated transformer action button.
    """
    send_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    image_pasted = pyqtSignal(Path)
    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatInputField")
        self.setPlaceholderText("Введите сообщение... (Enter — отправить, Shift+Enter — новая строка)")

        self.min_height = 42
        self.max_height = 250
        self.is_busy = False
        self.has_attachments = False
        self.is_engineer_mode = False

        self.setFixedHeight(self.min_height)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.setViewportMargins(6, 0, 42, 0)
        self.document().setDocumentMargin(6)

        self.setCursorWidth(2)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor("#F1F5F9"))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 240, 255, 110))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        self.setPalette(pal)

        self.setStyleSheet("""
            QTextEdit#ChatInputField {
                background-color: rgba(255, 255, 255, 0.12);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 6px 10px;
                font-size: 14px;
            }
            QTextEdit#ChatInputField:hover {
                background-color: rgba(255, 255, 255, 0.16);
                border: 1px solid rgba(255, 255, 255, 0.22);
            }
            QTextEdit#ChatInputField:focus {
                background-color: rgba(16, 24, 38, 0.92);
                border: 1px solid rgba(0, 240, 255, 0.45);
            }
        """)

        # Action Button (32x32 px)
        self.action_btn = QPushButton(self)
        self.action_btn.setObjectName("ChatInputTransformerBtn")
        self.action_btn.setFixedSize(32, 32)
        self.action_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.action_btn.clicked.connect(self._on_action_clicked)

        self._update_action_button_style()
        self._update_button_position()

        self.textChanged.connect(self._on_text_changed)

    def set_engineer_mode(self, is_engineer: bool):
        self.is_engineer_mode = is_engineer
        self._update_action_button_style()

    def _is_art_prompt(self, text: str) -> bool:
        if self.is_engineer_mode or not text:
            return False
        clean = text.strip().lower()
        if clean.startswith(("!art", "!арт", "!img", "!картинка")):
            return True
        art_triggers = [
            r'^(?:нарисуй|сгенерируй|сделай арт|изобрази|нарисуй мне|draw|generate art|paint)\b',
            r'\b(?:портрет|пейзаж|аниме арт|фотосессия|концепт-арт)\b'
        ]
        return any(bool(re.search(pat, clean)) for pat in art_triggers)

    def _on_text_changed(self):
        self._adjust_height()
        self._update_action_button_style()

    def _adjust_height(self):
        doc_h = int(self.document().size().height()) + 8
        target_h = max(self.min_height, min(doc_h, self.max_height))

        if abs(self.height() - target_h) >= 2:
            self.setFixedHeight(target_h)

        if doc_h > self.max_height:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._update_button_position()

    def _update_button_position(self):
        if hasattr(self, "action_btn") and self.action_btn:
            pad_right = 5
            pad_bottom = 5
            bx = self.width() - self.action_btn.width() - pad_right
            by = self.height() - self.action_btn.height() - pad_bottom
            self.action_btn.move(max(0, bx), max(0, by))

    def _update_action_button_style(self):
        if not hasattr(self, "action_btn") or not self.action_btn:
            return

        if self.is_busy:
            self.action_btn.setText("⏹")
            self.action_btn.setToolTip("Остановить генерацию (Stop)")
            self.action_btn.setStyleSheet("""
                QPushButton#ChatInputTransformerBtn {
                    background-color: rgba(255, 0, 127, 0.35);
                    color: #FFFFFF;
                    border: 1px solid #FF007F;
                    border-radius: 16px;
                    font-size: 13px;
                    font-weight: bold;
                    padding: 0px;
                    margin: 0px;
                }
                QPushButton#ChatInputTransformerBtn:hover {
                    background-color: rgba(255, 0, 127, 0.60);
                    color: #FFFFFF;
                }
                QPushButton#ChatInputTransformerBtn:pressed {
                    background-color: #FF007F;
                    color: #FFFFFF;
                }
            """)
        else:
            raw_text = self.toPlainText()
            has_content = bool(raw_text.strip()) or self.has_attachments
            self.action_btn.setText("➤")

            if has_content:
                if self._is_art_prompt(raw_text):
                    self.action_btn.setToolTip("Создать арт в Forge (Enter)")
                    self.action_btn.setStyleSheet("""
                        QPushButton#ChatInputTransformerBtn {
                            background-color: rgba(168, 85, 247, 0.45);
                            color: #FFFFFF;
                            border: 1px solid rgba(216, 180, 254, 0.60);
                            border-radius: 16px;
                            font-size: 13px;
                            font-weight: bold;
                            padding: 0px;
                            margin: 0px;
                        }
                        QPushButton#ChatInputTransformerBtn:hover {
                            background-color: rgba(168, 85, 247, 0.70);
                            border-color: #FFFFFF;
                        }
                        QPushButton#ChatInputTransformerBtn:pressed {
                            background-color: #9333EA;
                        }
                    """)
                else:
                    self.action_btn.setToolTip("Отправить сообщение (Enter)")
                    self.action_btn.setStyleSheet("""
                        QPushButton#ChatInputTransformerBtn {
                            background-color: rgba(0, 240, 255, 0.35);
                            color: #03131A;
                            border: 1px solid rgba(0, 240, 255, 0.65);
                            border-radius: 16px;
                            font-size: 13px;
                            font-weight: 900;
                            padding: 0px;
                            margin: 0px;
                        }
                        QPushButton#ChatInputTransformerBtn:hover {
                            background-color: rgba(0, 240, 255, 0.60);
                            color: #000000;
                            border-color: #FFFFFF;
                        }
                        QPushButton#ChatInputTransformerBtn:pressed {
                            background-color: #00F0FF;
                            color: #000000;
                        }
                    """)
            else:
                self.action_btn.setToolTip("Введите сообщение...")
                self.action_btn.setStyleSheet("""
                    QPushButton#ChatInputTransformerBtn {
                        background-color: rgba(255, 255, 255, 0.05);
                        color: #94A3B8;
                        border: none;
                        border-radius: 16px;
                        font-size: 13px;
                        font-weight: bold;
                        padding: 0px;
                        margin: 0px;
                    }
                    QPushButton#ChatInputTransformerBtn:hover {
                        background-color: rgba(255, 255, 255, 0.12);
                        color: #E2E8F0;
                    }
                """)

    def _on_action_clicked(self):
        if self.is_busy:
            self.stop_requested.emit()
        else:
            if self.toPlainText().strip() or self.has_attachments:
                self.send_requested.emit()

    def set_busy(self, is_busy: bool):
        self.is_busy = is_busy
        self._update_action_button_style()

    def set_has_attachments(self, has_attachments: bool):
        self.has_attachments = has_attachments
        self._update_action_button_style()

    def clear(self):
        super().clear()
        self.setFixedHeight(self.min_height)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._update_action_button_style()
        self._update_button_position()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_button_position()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                if not self.is_busy and (self.toPlainText().strip() or self.has_attachments):
                    self.send_requested.emit()
                event.accept()
                return

        if event.key() == Qt.Key.Key_V and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            clipboard = QApplication.clipboard()
            mime_data = clipboard.mimeData()
            if mime_data.hasImage():
                image: QImage = clipboard.image()
                if not image.isNull():
                    filename = f"clip_{int(time.time())}.png"
                    img_path = OUTPUTS_DIR / filename
                    image.save(str(img_path), "PNG")
                    self.image_pasted.emit(img_path)
                    event.accept()
                    return

        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.exists():
                    paths.append(p)
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)