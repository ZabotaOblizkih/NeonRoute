"""
Chat Stream Widget Component for NeonRoute.
Encapsulates SmoothScrollArea, ChatBackgroundViewport, dynamic message bubble layout,
automatic bottom-stickiness, and Ctrl+Wheel font scaling.
Modernized with QFluentWidgets components under Agentrules.md.
"""

from typing import Optional, TYPE_CHECKING
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFrame,
    QPushButton, QApplication
)
from qfluentwidgets import SmoothScrollArea
from ui.components.chat_background import ChatBackgroundViewport
from ui.components.chat_bubble import ChatMessageWidget
from utils.config_manager import ConfigManager

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ChatStreamWidget(QWidget):
    def __init__(self, window: 'MainWindow'):
        super().__init__(window)
        self.window = window
        self.font_size: int = self.window.app_config.get("chat_font_size", 13)
        self._btn_load_more: Optional[QPushButton] = None
        self._setup_ui()

    def _setup_ui(self):
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Smooth Scroll Area (Windows 11 Fluid Scrolling)
        self.scroll_area = SmoothScrollArea(self)
        self.scroll_area.setObjectName("ChatScrollArea")
        self.scroll_area.setStyleSheet("""
            QScrollArea#ChatScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.14);
                min-height: 24px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(0, 240, 255, 0.45);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # 2. Viewport with custom background support
        self.chat_viewport = ChatBackgroundViewport(self.scroll_area)
        self.chat_viewport.change_background_requested.connect(self.window._open_background_settings_dialog)
        self.chat_viewport.change_dimming_requested.connect(self.window._open_background_settings_dialog)
        self.chat_viewport.reset_background_requested.connect(self.window._on_background_reset)
        self.scroll_area.setViewport(self.chat_viewport)

        # Reinstall smooth scroll delegate on custom viewport
        if hasattr(self.scroll_area, "delegate") and self.scroll_area.delegate:
            self.chat_viewport.installEventFilter(self.scroll_area.delegate)

        # 3. Inner Container & Vertical Bubble Layout
        self.chat_container = QWidget()
        self.chat_container.setObjectName("ChatContainer")
        self.chat_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.chat_container.setAutoFillBackground(False)
        self.chat_container.setStyleSheet("background: transparent; background-color: transparent;")

        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(16, 16, 16, 16)
        self.chat_layout.setSpacing(12)
        self.chat_layout.addStretch()

        self.scroll_area.setWidget(self.chat_container)

        # 4. Auto bottom-stickiness управляется контроллером окна MainWindow
        pass

        # Install event filters for Ctrl+Wheel zoom
        self.scroll_area.installEventFilter(self)
        self.chat_viewport.installEventFilter(self)

        root_layout.addWidget(self.scroll_area)

    def eventFilter(self, source, event):
        if source in (self.scroll_area, self.scroll_area.viewport(), self.chat_viewport):
            if event.type() == QEvent.Type.Wheel:
                if QApplication.keyboardModifiers() == Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        self.change_font_size(1)
                    elif delta < 0:
                        self.change_font_size(-1)
                    return True
        return super().eventFilter(source, event)

    def change_font_size(self, delta: int):
        new_size = max(11, min(22, self.font_size + delta))
        if new_size != self.font_size:
            self.font_size = new_size
            self.window.chat_font_size = new_size
            self.window.app_config["chat_font_size"] = new_size
            ConfigManager.save_config(self.window.app_config)
            self.apply_font_size(new_size)
            self.window.statusBar().showMessage(f"Масштаб шрифта чата: {new_size} pt", 1500)

    def reset_font_size(self):
        self.font_size = 13
        self.window.chat_font_size = 13
        self.window.app_config["chat_font_size"] = 13
        ConfigManager.save_config(self.window.app_config)
        self.apply_font_size(13)
        self.window.statusBar().showMessage("Масштаб шрифта сброшен (13 pt)", 1500)

    def apply_font_size(self, size: int):
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), ChatMessageWidget):
                item.widget().set_font_size(size)

    def scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def create_load_more_button(self, remaining: int, on_click_callback) -> Optional[QPushButton]:
        if self._btn_load_more is not None:
            return self._btn_load_more
        if remaining <= 0:
            return None

        btn = QPushButton(f"▲ Загрузить более ранние сообщения (осталось: {remaining})", self.chat_container)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setFixedHeight(34)
        btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(15, 23, 42, 0.8), stop:1 rgba(30, 41, 59, 0.8));
                color: #38BDF8;
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 8px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
                margin: 4px 18px 8px 18px;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background: rgba(56, 189, 248, 0.15);
                border-color: #00F0FF;
                color: #00F0FF;
            }
        """)
        btn.clicked.connect(on_click_callback)
        self._btn_load_more = btn
        self.chat_layout.insertWidget(0, btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return btn

    def remove_load_more_button(self):
        if self._btn_load_more is not None:
            try:
                self.chat_layout.removeWidget(self._btn_load_more)
                self._btn_load_more.deleteLater()
            except Exception:
                pass
            self._btn_load_more = None

    def set_knowledge_sources(self, sources: list):
        """Пробрасывает метаданные источников знаний в активный виджет сообщения ассистента."""
        target = getattr(self.window, "_turn_bubble", None) or getattr(self.window, "_current_streaming_widget", None)
        if target is None and hasattr(self, "chat_layout"):
            for i in reversed(range(self.chat_layout.count())):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w and getattr(w, "is_user", None) is False:
                    target = w
                    break
        if target and hasattr(target, "set_knowledge_sources"):
            target.set_knowledge_sources(sources)
