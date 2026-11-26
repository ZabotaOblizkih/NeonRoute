"""
ui/log_console.py
=================
Современная консоль логов и отладки для NeonRoute в стиле Fluent Design.
- Наследуется от FramelessDialog с мягкими закругленными углами (border-radius: 14px)
- Кастомная шапка FluentTitleBar с кнопкой закрытия [X] в правом верхнем углу
- Динамическая адаптивная палитра (Material You / Fluent Tint) под цвет активных обоев
- Живой терминал логов с подсветкой синтаксиса уровней (INFO, WARNING, ERROR, CRITICAL, DEBUG)
- Фильтрация по уровням логов через SegmentedWidget (Все / INFO / WARNING / ERROR / DEBUG)
- Быстрый живой поиск по тексту логов через SearchLineEdit
- Fluent-кнопки управления: Копировать, Очистить, Экспорт в файл, Закрыть
- Потокобезопасная передача записей через QtLogHandler и QueuedConnection
"""

import os
import html
import logging
from collections import deque
from typing import Optional, Dict

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QTextCursor, QPixmap, QCursor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QLabel,
    QApplication,
    QFileDialog,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    PushButton,
    PrimaryPushButton,
    SearchLineEdit,
    SegmentedWidget,
    CaptionLabel,
    BodyLabel,
    FluentIcon as FIF,
)

from ui.common.palette_utils import extract_adaptive_palette

# Кольцевой буфер событий текущей сессии приложения (до 4000 записей)
_CURRENT_SESSION_LOGS = deque(maxlen=4000)


class QtLogHandler(QObject, logging.Handler):
    """
    Потокобезопасный logging.Handler, транслирующий записи логов в Qt-сигнал.
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
    """Инициализирует и регистрирует синглтон QtLogHandler на корневом логгере."""
    global _GLOBAL_QT_HANDLER
    if _GLOBAL_QT_HANDLER is None:
        _GLOBAL_QT_HANDLER = QtLogHandler()
        _GLOBAL_QT_HANDLER.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(_GLOBAL_QT_HANDLER)
    return _GLOBAL_QT_HANDLER


class LogConsoleDialog(FramelessDialog):
    """
    Современная интерактивная консоль текущей сессии приложения NeonRoute в стиле Fluent Design.
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.window = parent
        self.setWindowTitle("Консоль логов и отладки — NeonRoute")
        self.resize(920, 580)
        self.setMinimumSize(700, 420)

        # 1. Custom Fluent TitleBar с [X] в правом верхнем углу
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.close)

        # 2. Инициализация состояния фильтров
        self._active_filter = "ALL"
        self._search_text = ""
        self._current_palette: Dict[str, str] = {}

        self.setObjectName("LogConsoleDialog")
        self._setup_ui()
        self.update_adaptive_palette()
        self._load_session_logs()
        self._connect_logger()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои в иерархии родительских окон."""
        p = self.parent()
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = p.parent()
        return None

    def update_adaptive_palette(self):
        """
        Адаптирует цветовую палитру окна, карточки терминала и кнопок под активные обои.
        """
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            LogConsoleDialog#LogConsoleDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#LogConsoleBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #LogConsoleDialog QWidget {{
                background-color: transparent;
                color: #E2E8F0;
            }}
            QPlainTextEdit#LogTerminal {{
                background-color: {palette['terminal_bg']};
                border: 1px solid {palette['terminal_border']};
                border-radius: 10px;
                color: #E2E8F0;
                font-family: 'Consolas', 'Cascadia Code', 'Fira Code', 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.45;
                padding: 10px;
                selection-background-color: {palette['accent']};
                selection-color: #0A0D14;
            }}
            SearchLineEdit {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                color: #F8FAFC;
                padding-left: 8px;
            }}
            SearchLineEdit:focus {{
                border-color: {palette['accent']};
            }}
            PushButton, PrimaryPushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-weight: 500;
                font-size: 12px;
                padding: 5px 14px;
            }}
            PushButton:hover, PrimaryPushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PushButton:pressed, PrimaryPushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
            #LogConsoleDialog QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            #LogConsoleDialog QScrollBar::handle:vertical {{
                background: {palette['border']};
                min-height: 24px;
                border-radius: 4px;
            }}
            #LogConsoleDialog QScrollBar::handle:vertical:hover {{
                background: {palette['accent']};
            }}
        """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Контейнер содержимого под шапкой
        body_widget = QWidget(self)
        body_widget.setObjectName("LogConsoleBodyWidget")
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(18, 12, 18, 16)
        body_layout.setSpacing(12)

        # --- 1. Панель фильтров и поиска ---
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)

        # Сегментированный переключатель уровней логов
        self.level_filter = SegmentedWidget(body_widget)
        self.level_filter.addItem("ALL", "Все")
        self.level_filter.addItem("INFO", "Info")
        self.level_filter.addItem("WARNING", "Warning")
        self.level_filter.addItem("ERROR", "Error")
        self.level_filter.addItem("DEBUG", "Debug")
        self.level_filter.setCurrentItem("ALL")
        self.level_filter.currentItemChanged.connect(self._on_filter_changed)
        toolbar_layout.addWidget(self.level_filter)

        # Поле поиска
        self.search_box = SearchLineEdit(body_widget)
        self.search_box.setPlaceholderText("Поиск по тексту логов...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedWidth(240)
        self.search_box.textChanged.connect(self._on_search_changed)
        toolbar_layout.addWidget(self.search_box)

        toolbar_layout.addStretch(1)

        # Счетчик событий
        self.lbl_count = CaptionLabel("Событий: 0", body_widget)
        self.lbl_count.setStyleSheet("color: #94A3B8; font-size: 12px; font-weight: 500;")
        toolbar_layout.addWidget(self.lbl_count)

        body_layout.addLayout(toolbar_layout)

        # --- 2. Терминальное поле вывода логов ---
        self.text_edit = QPlainTextEdit(body_widget)
        self.text_edit.setObjectName("LogTerminal")
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(4000)
        body_layout.addWidget(self.text_edit, stretch=1)

        # --- 3. Нижняя панель действий и статус ---
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        self.btn_copy = PushButton("📋 Копировать логи", body_widget)
        self.btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_copy.setToolTip("Скопировать отображаемые логи в буфер обмена")
        self.btn_copy.clicked.connect(self._copy_logs)
        bottom_layout.addWidget(self.btn_copy)

        self.btn_clear = PushButton("🧹 Очистить", body_widget)
        self.btn_clear.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_clear.setToolTip("Очистить буфер журнала сессии")
        self.btn_clear.clicked.connect(self._clear_logs)
        bottom_layout.addWidget(self.btn_clear)

        self.btn_export = PushButton("💾 Сохранить в файл...", body_widget)
        self.btn_export.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_export.setToolTip("Экспортировать логи сессии в текстовый файл (.log)")
        self.btn_export.clicked.connect(self._export_logs)
        bottom_layout.addWidget(self.btn_export)

        self.btn_report = PushButton("🐞 Сообщить об ошибке", body_widget)
        self.btn_report.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_report.setToolTip("Открыть предпросмотр отчёта об ошибке с текущими логами")
        self.btn_report.clicked.connect(self._open_bug_report)
        bottom_layout.addWidget(self.btn_report)

        bottom_layout.addStretch(1)

        # Индикатор статуса
        self.status_lbl = CaptionLabel("", body_widget)
        self.status_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        bottom_layout.addWidget(self.status_lbl)

        self.btn_close = PushButton("Закрыть", body_widget)
        self.btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_close.clicked.connect(self.close)
        bottom_layout.addWidget(self.btn_close)

        body_layout.addLayout(bottom_layout)

        root_layout.addWidget(self.titleBar)
        root_layout.addWidget(body_widget, 1)

    def _on_filter_changed(self, route_key: str):
        """Обрабатывает выбор категории фильтра."""
        self._active_filter = route_key
        self._refresh_log_view()

    def _on_search_changed(self, text: str):
        """Обрабатывает изменение поискового запроса."""
        self._search_text = text
        self._refresh_log_view()

    def _load_session_logs(self):
        """Загружает логи из буфера текущей сессии."""
        self._refresh_log_view()

    def _refresh_log_view(self):
        """Перерисовывает содержимое консоли с учетом активного фильтра и поиска."""
        self.text_edit.clear()
        query = self._search_text.lower().strip()
        filter_level = self._active_filter.upper()

        color_map = {
            "INFO": "#38BDF8",      # Sky Cyan
            "WARNING": "#FBBF24",   # Amber
            "ERROR": "#F43F5E",     # Crimson
            "CRITICAL": "#FF0055",  # Neon Crimson
            "DEBUG": "#94A3B8"      # Slate Light
        }

        for msg, level in _CURRENT_SESSION_LOGS:
            lvl_upper = level.upper()
            if filter_level != "ALL" and lvl_upper != filter_level:
                continue
            if query and query not in msg.lower():
                continue

            color = color_map.get(lvl_upper, "#CBD5E1")
            escaped = html.escape(msg)
            self.text_edit.appendHtml(
                f"<div style='color: {color}; margin: 1px 0; white-space: pre-wrap;'>{escaped}</div>"
            )

        self.text_edit.moveCursor(QTextCursor.MoveOperation.End)
        self._update_counter()

    def _connect_logger(self):
        """Подключает сигнал живого поступления логов."""
        handler = setup_global_log_handler()
        handler.log_record_signal.connect(
            self._append_formatted_log,
            Qt.ConnectionType.QueuedConnection
        )

    def _append_formatted_log(self, text: str, levelname: str):
        """Добавляет одну запись лога в реальном времени, если она удовлетворяет фильтрам."""
        query = self._search_text.lower().strip()
        filter_level = self._active_filter.upper()
        lvl_upper = levelname.upper()

        matches_filter = (filter_level == "ALL" or lvl_upper == filter_level)
        matches_query = (not query or query in text.lower())

        if matches_filter and matches_query:
            # Защита от зависания QTextDocument при рендере гигантских строк (base64, дампы)
            render_text = text
            if len(render_text) > 8000:
                render_text = (
                    render_text[:4000]
                    + f"\n... [Текст обрезан в UI-консоли: {len(render_text)} симв.] ...\n"
                    + render_text[-4000:]
                )

            color_map = {
                "INFO": "#38BDF8",
                "WARNING": "#FBBF24",
                "ERROR": "#F43F5E",
                "CRITICAL": "#FF0055",
                "DEBUG": "#94A3B8"
            }
            color = color_map.get(lvl_upper, "#CBD5E1")
            escaped = html.escape(render_text)
            colored_html = f"<div style='color: {color}; margin: 1px 0; white-space: pre-wrap;'>{escaped}</div>"
            self.text_edit.appendHtml(colored_html)
            self.text_edit.moveCursor(QTextCursor.MoveOperation.End)

        self._update_counter()

    def _update_counter(self):
        total = len(_CURRENT_SESSION_LOGS)
        self.lbl_count.setText(f"Событий: {total}")

    def _copy_logs(self):
        """Копирует содержимое консоли в буфер обмена."""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_edit.toPlainText())
        self.status_lbl.setText("Логи скопированы в буфер обмена!")

    def _clear_logs(self):
        """Очищает консоль и текущий буфер логов."""
        _CURRENT_SESSION_LOGS.clear()
        self.text_edit.clear()
        self._update_counter()
        self.status_lbl.setText("Консоль логов очищена.")

    def _export_logs(self):
        """Экспорт текущих логов в файл."""
        from datetime import datetime
        default_name = f"neonroute_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить логи текущей сессии",
            default_name,
            "Log Files (*.log);;Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(self.text_edit.toPlainText())
                self.status_lbl.setText(f"Файл сохранен: {os.path.basename(file_path)}")
            except Exception as e:
                self.status_lbl.setText(f"Ошибка экспорта: {e}")

    def _open_bug_report(self):
        """Закрывает консоль логов и открывает диалог отправки отчёта об ошибке."""
        from PyQt6.QtCore import QTimer

        parent_win = self.window or self.parent()
        lines = self.text_edit.toPlainText().splitlines()
        error_lines = [ln for ln in lines if "[ERROR]" in ln or "[CRITICAL]" in ln]
        summary = error_lines[-1] if error_lines else ""

        self.close()
        if parent_win is not None and hasattr(parent_win, "open_bug_report_dialog"):
            QTimer.singleShot(
                0,
                lambda: parent_win.open_bug_report_dialog(
                    error_summary=summary,
                    source="log_console",
                ),
            )
        else:
            def _open_standalone():
                from ui.dialogs.bug_report_dialog import BugReportDialog

                dlg = BugReportDialog(parent=None, error_summary=summary, source="log_console")
                dlg.exec()

            QTimer.singleShot(0, _open_standalone)

    def showEvent(self, event):
        super().showEvent(event)
        # Гарантируем корректное отображение кнопки закрытия [X]
        if hasattr(self, "titleBar") and self.titleBar:
            if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
                self.titleBar.minBtn.hide()
            if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
                self.titleBar.maxBtn.hide()
            if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
                self.titleBar.closeBtn.setVisible(True)

        self.update_adaptive_palette()

        if self.parent():
            try:
                parent_geom = self.parent().geometry()
                x = parent_geom.x() + (parent_geom.width() - self.width()) // 2
                y = parent_geom.y() + (parent_geom.height() - self.height()) // 2
                self.move(max(20, x), max(20, y))
            except Exception:
                pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        try:
            handler = setup_global_log_handler()
            handler.log_record_signal.disconnect(self._append_formatted_log)
        except Exception:
            pass
        super().closeEvent(event)
