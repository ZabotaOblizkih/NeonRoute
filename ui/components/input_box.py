"""
Cyberpunk / Glassmorphic Multi-line Input Box for NeonRoute.
Features:
- Integrated circular Fluent action button (36x36 px): Send (FIF.SEND) ⇄ Stop (⏹)
- Dynamic Mode Highlighting: Button turns Neon Cyan (#00F0FF) on text entry,
  Soft Purple (#C084FC) on !art prompt, and Warning Crimson (#FF007F) during generation
- Viewport right padding (48px) to prevent text overlap with action button
- Dynamic smooth height expansion (42px base up to 250px)
- Enter to send, Shift+Enter for newline
- Direct clipboard image capture (Ctrl+V)
- Drag and drop file support
- Guaranteed translucent Glassmorphism styling with outline disabled
"""

import time
import re
from pathlib import Path
from typing import Optional, List

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QKeyEvent, QImage, QCursor, QColor, QPalette, QIcon, QPixmap, QPainter
from PyQt6.QtWidgets import QTextEdit, QApplication
from qfluentwidgets import (
    TransparentToolButton,
    FluentIcon as FIF,
    ToolTipFilter,
    ToolTipPosition,
)

from config import OUTPUTS_DIR


def _create_stop_square_icon(color: str = "#FF007F", size: int = 32) -> QIcon:
    """Creates a high-DPI rounded square stop icon."""
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    # Centered 14x14 square with 2.5px radius
    offset = (size - 14) / 2.0
    painter.drawRoundedRect(int(offset), int(offset), 14, 14, 2.5, 2.5)
    painter.end()
    return QIcon(pix)


class ChatInputBox(QTextEdit):
    """
    Modern glassmorphic multi-line chat input with dynamic mode highlighting,
    auto-expansion up to 250px, and integrated circular Fluent action button.
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

        # Ensure right margin keeps text well away from the 36px action button
        self.setViewportMargins(8, 0, 48, 0)
        self.document().setDocumentMargin(6)

        self.setCursorWidth(2)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor("#F1F5F9"))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 240, 255, 110))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        self.setPalette(pal)

        # Deep Glassmorphism: semi-transparent base (rgba(15, 23, 42, 0.65)), thin border, no outline
        self.setStyleSheet("""
            QTextEdit#ChatInputField, ChatInputBox#ChatInputField {
                background-color: rgba(15, 23, 42, 0.65);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 11px;
                padding: 6px 10px;
                font-size: 14px;
                outline: none;
            }
            QTextEdit#ChatInputField:hover, ChatInputBox#ChatInputField:hover {
                background-color: rgba(15, 23, 42, 0.75);
                border: 1px solid rgba(0, 240, 255, 0.35);
                outline: none;
            }
            QTextEdit#ChatInputField:focus, ChatInputBox#ChatInputField:focus {
                background-color: rgba(15, 23, 42, 0.88);
                border: 1px solid rgba(0, 240, 255, 0.65);
                outline: none;
            }
        """)

        # Circular Fluent UI Transformer Action Button (36x36 px)
        self.action_btn = TransparentToolButton(parent=self)
        self.action_btn.setObjectName("ChatInputTransformerBtn")
        self.action_btn.setFixedSize(36, 36)
        self.action_btn.setIconSize(QSize(18, 18))
        self.action_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.action_btn.installEventFilter(
            ToolTipFilter(self.action_btn, showDelay=300, position=ToolTipPosition.TOP)
        )
        self.action_btn.clicked.connect(self._on_action_clicked)

        # Pre-create icon variants for optimal performance
        self._icon_idle = FIF.SEND.colored(QColor("#64748B"), QColor("#94A3B8"))
        self._icon_cyan = FIF.SEND.colored(QColor("#00F0FF"), QColor("#00F0FF"))
        self._icon_purple = FIF.SEND.colored(QColor("#C084FC"), QColor("#C084FC"))
        self._icon_stop = _create_stop_square_icon("#FF007F", 32)

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
            # Center vertically when at base single-line height, otherwise anchor near bottom
            if self.height() <= self.min_height + 4:
                by = max(0, (self.height() - self.action_btn.height()) // 2)
            else:
                by = max(0, self.height() - self.action_btn.height() - 4)
            bx = self.width() - self.action_btn.width() - pad_right
            self.action_btn.move(max(0, bx), max(0, by))

    def _update_action_button_style(self):
        if not hasattr(self, "action_btn") or not self.action_btn:
            return

        if self.is_busy:
            self.action_btn.setIcon(self._icon_stop)
            self.action_btn.setText("⏹")
            self.action_btn.setToolTip("Остановить генерацию (Stop)")
            self.action_btn.setStyleSheet("""
                TransparentToolButton#ChatInputTransformerBtn {
                    background-color: rgba(255, 0, 127, 0.25);
                    border: 1px solid #FF007F;
                    border-radius: 18px;
                }
                TransparentToolButton#ChatInputTransformerBtn:hover {
                    background-color: rgba(255, 0, 127, 0.50);
                    border-color: #FF2E93;
                }
                TransparentToolButton#ChatInputTransformerBtn:pressed {
                    background-color: rgba(255, 0, 127, 0.75);
                }
            """)
        else:
            raw_text = self.toPlainText()
            has_content = bool(raw_text.strip()) or self.has_attachments
            self.action_btn.setText("➤")

            if has_content:
                if self._is_art_prompt(raw_text):
                    self.action_btn.setIcon(self._icon_purple)
                    self.action_btn.setToolTip("Создать арт в Forge (Enter)")
                    self.action_btn.setStyleSheet("""
                        TransparentToolButton#ChatInputTransformerBtn {
                            background-color: rgba(192, 132, 252, 0.22);
                            border: 1px solid #C084FC;
                            border-radius: 18px;
                        }
                        TransparentToolButton#ChatInputTransformerBtn:hover {
                            background-color: rgba(192, 132, 252, 0.45);
                            border-color: #E9D5FF;
                        }
                        TransparentToolButton#ChatInputTransformerBtn:pressed {
                            background-color: rgba(192, 132, 252, 0.65);
                        }
                    """)
                else:
                    self.action_btn.setIcon(self._icon_cyan)
                    self.action_btn.setToolTip("Отправить сообщение (Enter)")
                    self.action_btn.setStyleSheet("""
                        TransparentToolButton#ChatInputTransformerBtn {
                            background-color: rgba(0, 240, 255, 0.18);
                            border: 1px solid #00F0FF;
                            border-radius: 18px;
                        }
                        TransparentToolButton#ChatInputTransformerBtn:hover {
                            background-color: rgba(0, 240, 255, 0.40);
                            border-color: #00F0FF;
                        }
                        TransparentToolButton#ChatInputTransformerBtn:pressed {
                            background-color: rgba(0, 240, 255, 0.60);
                        }
                    """)
            else:
                self.action_btn.setIcon(self._icon_idle)
                self.action_btn.setToolTip("Введите сообщение...")
                self.action_btn.setStyleSheet("""
                    TransparentToolButton#ChatInputTransformerBtn {
                        background-color: rgba(255, 255, 255, 0.04);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 18px;
                    }
                    TransparentToolButton#ChatInputTransformerBtn:hover {
                        background-color: rgba(255, 255, 255, 0.12);
                        border: 1px solid rgba(255, 255, 255, 0.20);
                    }
                    TransparentToolButton#ChatInputTransformerBtn:pressed {
                        background-color: rgba(255, 255, 255, 0.08);
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
