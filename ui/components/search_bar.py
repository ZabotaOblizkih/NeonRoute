"""
Search Bar Component for NeonRoute Chat.
Provides Ctrl+F in-chat search, match counting, next/previous navigation,
and smooth scrolling to highlighted message.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLineEdit, QLabel, QPushButton
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
                background: rgba(18, 24, 38, 0.94);
                border: 1px solid #00F0FF;
                border-radius: 8px;
                padding: 4px 8px;
            }
            QLineEdit {
                background: #0D131F;
                color: #FFFFFF;
                border: 1px solid #1E2D47;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #00F0FF;
            }
            QLabel {
                color: #94A3B8;
                font-size: 12px;
                padding: 0 4px;
            }
            QPushButton {
                background: #1A2438;
                color: #CBD5E1;
                border: 1px solid #263550;
                border-radius: 4px;
                padding: 3px 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #253452;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            #CloseSearchBtn {
                background: transparent;
                border: none;
                color: #94A3B8;
                font-size: 16px;
                font-weight: bold;
            }
            #CloseSearchBtn:hover {
                color: #FF0055;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        lbl_icon = QLabel("🔍", self)
        layout.addWidget(lbl_icon)

        self.input_field = QLineEdit(self)
        self.input_field.setPlaceholderText("Поиск в диалоге... (Esc для закрытия)")
        self.input_field.textChanged.connect(self.search_changed.emit)
        self.input_field.returnPressed.connect(self.next_requested.emit)
        layout.addWidget(self.input_field, stretch=1)

        self.lbl_count = QLabel("0 из 0", self)
        layout.addWidget(self.lbl_count)

        self.btn_prev = QPushButton("▲", self)
        self.btn_prev.setToolTip("Предыдущее совпадение (Shift+Enter)")
        self.btn_prev.clicked.connect(self.prev_requested.emit)
        layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton("▼", self)
        self.btn_next.setToolTip("Следующее совпадение (Enter)")
        self.btn_next.clicked.connect(self.next_requested.emit)
        layout.addWidget(self.btn_next)

        self.btn_close = QPushButton("✕", self)
        self.btn_close.setObjectName("CloseSearchBtn")
        self.btn_close.setToolTip("Закрыть поиск (Esc)")
        self.btn_close.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.btn_close)

    def set_match_status(self, current: int, total: int):
        """Updates match counter label."""
        if total == 0:
            self.lbl_count.setText("0 совпадений")
            self.lbl_count.setStyleSheet("color: #F87171; font-size: 12px;")
        else:
            self.lbl_count.setText(f"{current} из {total}")
            self.lbl_count.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: bold;")

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
