"""
Search Bar Component for NeonRoute Chat.
Provides Ctrl+F in-chat search, match counting, next/previous navigation,
and smooth scrolling to highlighted message.
Modernized with QFluentWidgets components and Glassmorphism styling under Agentrules.md.
"""

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
from qfluentwidgets import (
    SearchLineEdit,
    TransparentToolButton,
    FluentIcon as FIF,
    ToolTipFilter,
    ToolTipPosition,
)


class SearchBarWidget(QFrame):
    """
    Compact popup / top search bar for searching text across chat messages.
    """
    search_changed = pyqtSignal(str)
    next_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    close_requested = pyqtSignal()

    # Aliases
    query_changed = search_changed
    find_next = next_requested
    find_prev = prev_requested
    closed = close_requested

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SearchBarWidget")
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            #SearchBarWidget {
                background-color: rgba(15, 23, 42, 0.90);
                border: 1px solid rgba(0, 240, 255, 0.28);
                border-radius: 12px;
                padding: 3px 8px;
            }
            SearchLineEdit {
                background-color: rgba(10, 15, 26, 0.75);
                color: #F1F5F9;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 4px 8px;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                min-width: 200px;
            }
            SearchLineEdit:focus {
                border: 1px solid rgba(0, 240, 255, 0.50);
                background-color: rgba(15, 23, 42, 0.90);
            }
            QLabel#SearchCountLabel {
                color: #94A3B8;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
                padding: 0 4px;
                background: transparent;
            }
            TransparentToolButton {
                border-radius: 6px;
            }
            TransparentToolButton:hover {
                background-color: rgba(0, 240, 255, 0.15);
            }
            TransparentToolButton#CloseSearchBtn:hover {
                background-color: rgba(239, 68, 68, 0.20);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self.input_field = SearchLineEdit(self)
        self.input_field.setPlaceholderText("Поиск в диалоге... (Esc для закрытия)")
        self.input_field.textChanged.connect(self.search_changed.emit)
        self.input_field.installEventFilter(self)
        layout.addWidget(self.input_field, stretch=1)

        self.lbl_count = QLabel("0 совпадений", self)
        self.lbl_count.setObjectName("SearchCountLabel")
        layout.addWidget(self.lbl_count)

        self.btn_prev = TransparentToolButton(FIF.UP, self)
        self.btn_prev.setFixedSize(28, 28)
        self.btn_prev.setIconSize(QSize(12, 12))
        self.btn_prev.setToolTip("Предыдущее совпадение (Shift+Enter)")
        self.btn_prev.installEventFilter(ToolTipFilter(self.btn_prev, 300, ToolTipPosition.BOTTOM))
        self.btn_prev.clicked.connect(self.prev_requested.emit)
        layout.addWidget(self.btn_prev)

        self.btn_next = TransparentToolButton(FIF.DOWN, self)
        self.btn_next.setFixedSize(28, 28)
        self.btn_next.setIconSize(QSize(12, 12))
        self.btn_next.setToolTip("Следующее совпадение (Enter)")
        self.btn_next.installEventFilter(ToolTipFilter(self.btn_next, 300, ToolTipPosition.BOTTOM))
        self.btn_next.clicked.connect(self.next_requested.emit)
        layout.addWidget(self.btn_next)

        self.btn_close = TransparentToolButton(FIF.CLOSE, self)
        self.btn_close.setObjectName("CloseSearchBtn")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setIconSize(QSize(12, 12))
        self.btn_close.setToolTip("Закрыть поиск (Esc)")
        self.btn_close.installEventFilter(ToolTipFilter(self.btn_close, 300, ToolTipPosition.BOTTOM))
        self.btn_close.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.btn_close)

    def eventFilter(self, watched, event):
        if watched == self.input_field and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.prev_requested.emit()
                else:
                    self.next_requested.emit()
                return True
            elif event.key() == Qt.Key.Key_Escape:
                self.close_requested.emit()
                return True
        return super().eventFilter(watched, event)

    def set_match_status(self, current: int, total: int):
        """Updates match counter label."""
        if total == 0:
            self.lbl_count.setText("0 совпадений")
            self.lbl_count.setStyleSheet("color: rgba(248, 113, 113, 0.85); font-size: 11px;")
        else:
            self.lbl_count.setText(f"{current} из {total}")
            self.lbl_count.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: 600;")

    def set_match_info(self, current: int, total: int):
        self.set_match_status(current, total)

    @property
    def search_input(self):
        return self.input_field

    @property
    def counter_label(self):
        return self.lbl_count

    def focus_input(self):
        """Activates and selects text in the search input."""
        self.input_field.setFocus()
        self.input_field.selectAll()

    def show_and_focus(self):
        self.setVisible(True)
        self.focus_input()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
