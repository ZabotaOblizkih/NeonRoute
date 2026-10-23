"""
Log Console Dialog for NeonRoute.
Provides real-time terminal log viewer, syntax coloring by log level,
and clipboard export capabilities.
Displays strictly the current application runtime session logs.
"""

import html
import logging
from collections import deque
from typing import Optional, Tuple
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QPlainTextEdit, QLabel, QApplication
)

# Lightweight ring buffer storing up to 2000 events strictly for the current app run (~2MB RAM)
_CURRENT_SESSION_LOGS = deque(maxlen=2000)


class QtLogHandler(QObject, logging.Handler):
    """
    Thread-safe logging.Handler that emits PyQt signals when new log records arrive.
    """
    log_record_signal = pyqtSignal(str, str)  # formatted_message, levelname

    def __init__(self):
        super().__init__()
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"))

    def emit(self, record):
        try:
            msg = self.format(record)
            _CURRENT_SESSION_LOGS.append((msg, record.levelname))
            self.log_record_signal.emit(msg, record.levelname)
        except Exception:
            pass


_GLOBAL_QT_HANDLER: Optional[QtLogHandler] = None


def setup_global_log_handler() -> QtLogHandler:
    """Initializes and registers the singleton QtLogHandler on root logger."""
    global _GLOBAL_QT_HANDLER
    if _GLOBAL_QT_HANDLER is None:
        _GLOBAL_QT_HANDLER = QtLogHandler()
        _GLOBAL_QT_HANDLER.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(_GLOBAL_QT_HANDLER)
    return _GLOBAL_QT_HANDLER


class LogConsoleDialog(QDialog):
    """
    Interactive dark terminal dialog displaying current session application logs in real-time.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Консоль логов — NeonRoute (Текущая сессия)")
        self.resize(880, 540)
        self.setMinimumSize(640, 400)

        self._setup_ui()
        self._load_session_logs()
        self._connect_logger()

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #080B10;
                color: #CBD5E1;
            }
            QPlainTextEdit {
                background-color: #04060A;
                color: #CBD5E1;
                border: 1px solid #1E283D;
                border-radius: 6px;
                padding: 10px;
                font-family: 'Consolas', 'Cascadia Code', 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.45;
            }
            QPushButton {
                background-color: #141C2A;
                color: #E2E8F0;
                border: 1px solid #24344D;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #1C273C;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton:pressed {
                background-color: #0F1622;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Header bar
        header_layout = QHBoxLayout()
        title_lbl = QLabel("🖥️ Журнал текущей сессии (Live Console)")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #00F0FF;")
        header_layout.addWidget(title_lbl)

        header_layout.addStretch()

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        header_layout.addWidget(self.status_lbl)

        layout.addLayout(header_layout)

        # Log Text Box
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(4000)
        layout.addWidget(self.text_edit, stretch=1)

        # Buttons
        btn_layout = QHBoxLayout()

        self.btn_copy = QPushButton("📋 Копировать логи")
        self.btn_copy.setToolTip("Скопировать весь текст логов в буфер обмена")
        self.btn_copy.clicked.connect(self._copy_logs)
        btn_layout.addWidget(self.btn_copy)

        self.btn_clear = QPushButton("🧹 Очистить")
        self.btn_clear.setToolTip("Очистить виджет консоли")
        self.btn_clear.clicked.connect(self._clear_logs)
        btn_layout.addWidget(self.btn_clear)

        btn_layout.addStretch()

        self.btn_close = QPushButton("Закрыть")
        self.btn_close.clicked.connect(self.close)
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)

    def _load_session_logs(self):
        """Populates the console strictly from current runtime session buffer."""
        self.text_edit.clear()
        for msg, level in _CURRENT_SESSION_LOGS:
            self._append_formatted_log(msg, level)

    def _connect_logger(self):
        handler = setup_global_log_handler()
        handler.log_record_signal.connect(
            self._append_formatted_log,
            Qt.ConnectionType.QueuedConnection
        )

    def _append_formatted_log(self, text: str, levelname: str):
        color_map = {
            "INFO": "#38BDF8",      # Sky Cyan
            "WARNING": "#FBBF24",   # Amber
            "ERROR": "#F43F5E",     # Crimson
            "CRITICAL": "#FF0055",  # Neon Crimson
            "DEBUG": "#64748B"      # Slate
        }
        color = color_map.get(levelname.upper(), "#CBD5E1")

        escaped = html.escape(text)
        colored_html = f"<div style='color: {color}; margin: 1px 0;'>{escaped}</div>"
        self.text_edit.appendHtml(colored_html)
        self.text_edit.moveCursor(QTextCursor.MoveOperation.End)

    def _copy_logs(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_edit.toPlainText())
        self.status_lbl.setText("Логи скопированы в буфер обмена!")

    def _clear_logs(self):
        self.text_edit.clear()
        self.status_lbl.setText("Консоль логов очищена.")