"""
ui/dialogs/bug_report_dialog.py
===============================
Модальный диалог предпросмотра и ручной отправки отчёта об ошибке в стиле Fluent Design.
Пользователь видит полный очищенный от секретов текст отчёта, может отредактировать
заголовок и содержимое и явно инициировать отправку на Cloudflare Worker.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    FluentTitleBar,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)

from config import APP_VERSION
from services.bug_report_service import (
    BugReportPayload,
    BugReportResult,
    MAX_TITLE_CHARS,
    build_bug_report_payload,
    get_bug_report_endpoint,
)
from ui.common.palette_utils import extract_adaptive_palette
from ui.common.toast_helper import show_toast
from ui.workers.bug_report_worker import BugReportSendWorker

logger = logging.getLogger("NeonRoute.BugReportDialog")


class BugReportDialog(FramelessDialog):
    """Модальный диалог предпросмотра, редактирования и отправки отчёта об ошибке."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        error_summary: str = "",
        traceback_text: str = "",
        source: str = "manual",
    ):
        super().__init__(parent=parent)
        self.window = parent
        self.setWindowTitle(f"Отчёт об ошибке — NeonRoute v{APP_VERSION}")
        self.resize(760, 620)
        self.setMinimumSize(600, 480)

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

        self.setObjectName("BugReportDialog")
        self._current_palette: Dict[str, str] = {}
        self._send_worker: Optional[BugReportSendWorker] = None
        self._source = source or "manual"
        self._payload: BugReportPayload = build_bug_report_payload(
            error_summary=error_summary,
            traceback_text=traceback_text,
            source=self._source,
        )

        self._setup_ui()
        self.update_adaptive_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
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

    def update_adaptive_palette(self) -> None:
        palette = extract_adaptive_palette(self._resolve_pixmap())
        self._current_palette = palette

        self.setStyleSheet(f"""
            BugReportDialog#BugReportDialog {{
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
            QWidget#BugReportBody {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #BugReportDialog QWidget {{
                background-color: transparent;
                color: #CBD5E1;
            }}
            QFrame#PrivacyCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
                padding: 10px 14px;
            }}
            QLineEdit#BugTitleEdit, QPlainTextEdit#BugBodyEdit {{
                background-color: {palette['terminal_bg']};
                border: 1px solid {palette['terminal_border']};
                border-radius: 8px;
                padding: 8px 10px;
                color: #E2E8F0;
                font-family: 'Consolas', 'Cascadia Mono', 'Segoe UI', monospace;
                font-size: 12px;
            }}
            QLineEdit#BugTitleEdit {{
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 13px;
                font-weight: 600;
            }}
        """)

    def _setup_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        body_widget = QWidget(self)
        body_widget.setObjectName("BugReportBody")
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(22, 14, 22, 18)
        body_layout.setSpacing(12)

        hdr = SubtitleLabel("Сообщить об ошибке", body_widget)
        hdr.setStyleSheet("color: #F8FAFC; font-weight: 700; font-size: 18px;")
        body_layout.addWidget(hdr)

        # Карточка приватности
        privacy_card = QFrame(body_widget)
        privacy_card.setObjectName("PrivacyCard")
        privacy_layout = QVBoxLayout(privacy_card)
        privacy_layout.setContentsMargins(12, 10, 12, 10)
        privacy_layout.setSpacing(4)

        privacy_title = BodyLabel("Приватность и предпросмотр данных", privacy_card)
        privacy_title.setStyleSheet("color: #F8FAFC; font-weight: 600; font-size: 13px;")
        privacy_layout.addWidget(privacy_title)

        privacy_desc = CaptionLabel(
            "В отчёт включены версия приложения, параметры ОС/GPU/RAM и свежие логи без переписки. "
            "Пути пользователя Windows и известные токены/ключи автоматически замаскированы. "
            "Отправка выполняется только после нажатия кнопки «Отправить отчёт» — вы можете отредактировать текст ниже.",
            privacy_card,
        )
        privacy_desc.setWordWrap(True)
        privacy_desc.setStyleSheet("color: #94A3B8; font-size: 12px;")
        privacy_layout.addWidget(privacy_desc)
        body_layout.addWidget(privacy_card)

        # Заголовок отчёта
        lbl_title = CaptionLabel("Заголовок (title):", body_widget)
        lbl_title.setStyleSheet("color: #CBD5E1; font-weight: 600; font-size: 12px;")
        body_layout.addWidget(lbl_title)

        self.title_edit = QLineEdit(body_widget)
        self.title_edit.setObjectName("BugTitleEdit")
        self.title_edit.setMaxLength(MAX_TITLE_CHARS)
        self.title_edit.setText(self._payload.title)
        self.title_edit.setPlaceholderText("Краткое описание проблемы...")
        body_layout.addWidget(self.title_edit)

        # Тело отчёта (Markdown)
        lbl_body = CaptionLabel("Содержимое отчёта (Markdown, будет отправлено как есть):", body_widget)
        lbl_body.setStyleSheet("color: #CBD5E1; font-weight: 600; font-size: 12px;")
        body_layout.addWidget(lbl_body)

        self.body_edit = QPlainTextEdit(body_widget)
        self.body_edit.setObjectName("BugBodyEdit")
        self.body_edit.setPlainText(self._payload.body)
        body_layout.addWidget(self.body_edit, stretch=1)

        # Строка статуса
        self.status_lbl = QLabel("", body_widget)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setOpenExternalLinks(True)
        self.status_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        body_layout.addWidget(self.status_lbl)

        # Кнопки действий
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch(1)

        self.btn_cancel = PushButton("Отмена", body_widget)
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.clicked.connect(self.close)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_send = PrimaryPushButton(FIF.SEND, "Отправить отчёт", body_widget)
        self.btn_send.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_send.clicked.connect(self._on_send_clicked)
        btn_layout.addWidget(self.btn_send)

        body_layout.addLayout(btn_layout)

        root_layout.addWidget(self.titleBar)
        root_layout.addWidget(body_widget, 1)

    def _set_busy(self, busy: bool) -> None:
        self.title_edit.setEnabled(not busy)
        self.body_edit.setEnabled(not busy)
        self.btn_send.setEnabled(not busy)
        self.btn_send.setText("Отправка..." if busy else "Отправить отчёт")

    def _on_send_clicked(self) -> None:
        if self._send_worker is not None and self._send_worker.isRunning():
            return

        title = self.title_edit.text().strip()
        body = self.body_edit.toPlainText().strip()
        if not title or not body:
            self.status_lbl.setStyleSheet("color: #FBBF24; font-size: 12px;")
            self.status_lbl.setText("Заполните заголовок и текст отчёта перед отправкой.")
            show_toast(self, "Неполные данные", "Укажите заголовок и текст отчёта.", kind="warning")
            return

        endpoint_url = get_bug_report_endpoint()
        if not endpoint_url:
            msg = (
                "URL прокси отправки отчётов не настроен "
                "(задайте NEONROUTE_BUG_REPORT_URL или параметр bug_report_endpoint_url)."
            )
            self.status_lbl.setStyleSheet("color: #FBBF24; font-size: 12px;")
            self.status_lbl.setText(msg)
            show_toast(self, "Отправка недоступна", msg, kind="warning")
            return

        self._set_busy(True)
        self.status_lbl.setStyleSheet("color: #38BDF8; font-size: 12px;")
        self.status_lbl.setText("Отправка отчёта на сервер...")

        worker = BugReportSendWorker(
            endpoint_url=endpoint_url,
            title=title,
            body=body,
            app_version=self._payload.app_version,
            category=self._source,
        )
        self._send_worker = worker
        worker.finished_with_result.connect(self._on_send_result)
        worker.error_occurred.connect(self._on_send_error)
        worker.finished.connect(self._cleanup_worker)
        worker.start()

    def _on_send_result(self, result: object) -> None:
        self._set_busy(False)
        if not isinstance(result, BugReportResult):
            self._on_send_error("Некорректный ответ воркера отправки.")
            return

        if result.ok:
            self.status_lbl.setStyleSheet("color: #34D399; font-size: 12px;")
            if result.issue_url:
                self.status_lbl.setText(
                    f"✅ {result.message} "
                    f"<a href='{result.issue_url}' style='color: #38BDF8;'>Открыть Issue</a>"
                )
            else:
                self.status_lbl.setText(f"✅ {result.message}")
            self.btn_cancel.setText("Закрыть")
            show_toast(self, "Отчёт отправлен", result.message, kind="success")
        else:
            self.status_lbl.setStyleSheet("color: #F43F5E; font-size: 12px;")
            self.status_lbl.setText(f"❌ {result.message}")
            show_toast(self, "Ошибка отправки", result.message, kind="error")

    def _on_send_error(self, error_msg: str) -> None:
        self._set_busy(False)
        self.status_lbl.setStyleSheet("color: #F43F5E; font-size: 12px;")
        self.status_lbl.setText(f"❌ Ошибка отправки: {error_msg}")
        show_toast(self, "Ошибка отправки", error_msg, kind="error")

    def _cleanup_worker(self) -> None:
        worker = self._send_worker
        self._send_worker = None
        if worker is not None:
            worker.deleteLater()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        worker = self._send_worker
        if worker is not None:
            try:
                worker.finished_with_result.disconnect(self._on_send_result)
                worker.error_occurred.disconnect(self._on_send_error)
                worker.finished.disconnect(self._cleanup_worker)
            except Exception:
                pass
            if worker.isRunning():
                worker.wait(500)
            self._cleanup_worker()
        super().closeEvent(event)
