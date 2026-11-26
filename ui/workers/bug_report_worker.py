"""
ui/workers/bug_report_worker.py
===============================
Фоновый QThread-воркер (parent=None) для неблокирующей отправки отчёта об ошибке
на Cloudflare Worker.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from services.bug_report_service import send_bug_report

logger = logging.getLogger("NeonRoute.BugReportWorker")


class BugReportSendWorker(QThread):
    """Фоновый поток отправки отчёта об ошибке через Cloudflare Worker."""

    finished_with_result = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        endpoint_url: str,
        title: str,
        body: str,
        app_version: str = "",
        category: str = "manual",
        timeout: float = 15.0,
    ):
        super().__init__(parent=None)
        self._endpoint_url = endpoint_url
        self._title = title
        self._body = body
        self._app_version = app_version
        self._category = category
        self._timeout = timeout

    def run(self) -> None:
        try:
            result = send_bug_report(
                endpoint_url=self._endpoint_url,
                title=self._title,
                body=self._body,
                timeout=self._timeout,
                app_version=self._app_version,
                category=self._category,
            )
            self.finished_with_result.emit(result)
        except Exception as exc:
            logger.error(f"[BugReportWorker] Непредвиденная ошибка отправки: {exc}")
            self.error_occurred.emit(str(exc))
