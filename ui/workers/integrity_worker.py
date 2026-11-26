"""
ui/workers/integrity_worker.py
==============================
Фоновые QThread-воркеры (parent=None) для проверки целостности при старте,
проверки обновлений на GitHub Releases и выборочного применения обновлений.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from services.integrity_service import (
    GITHUB_REPO,
    ThreeWayFileEntry,
    analyze_three_way_updates,
    download_and_apply_selective_update,
    fetch_remote_release_manifest,
    verify_local_integrity,
)

logger = logging.getLogger("NeonRoute.IntegrityWorker")


class LocalIntegrityWorker(QThread):
    """Фоновая warn-only проверка локальной целостности при старте."""

    check_completed = pyqtSignal(object)

    def __init__(self, base_dir: Path):
        super().__init__(parent=None)
        self._base_dir = Path(base_dir)

    def run(self) -> None:
        report = verify_local_integrity(self._base_dir, log_warnings=True)
        self.check_completed.emit(report)


class RemoteUpdateCheckWorker(QThread):
    """Фоновая загрузка удалённого release_manifest.json и 3-way анализ."""

    check_finished = pyqtSignal(object, object, object)

    def __init__(self, base_dir: Path, current_version: str, repo: str = GITHUB_REPO):
        super().__init__(parent=None)
        self._base_dir = Path(base_dir)
        self._current_version = current_version
        self._repo = repo

    def run(self) -> None:
        try:
            remote_manifest, release_meta, err = fetch_remote_release_manifest(
                repo=self._repo,
                timeout=8.0,
            )
            if err or remote_manifest is None:
                self.check_finished.emit(None, release_meta, err or "Не удалось получить манифест.")
                return

            report = analyze_three_way_updates(
                base_dir=self._base_dir,
                remote_manifest=remote_manifest,
                current_app_version=self._current_version,
            )
            # Сохраняем remote_manifest в release_meta для последующего применения
            meta_with_manifest = dict(release_meta or {})
            meta_with_manifest["remote_manifest"] = remote_manifest
            self.check_finished.emit(report, meta_with_manifest, None)
        except Exception as e:
            logger.warning(f"[IntegrityWorker] Ошибка проверки обновлений: {e}")
            self.check_finished.emit(None, None, str(e))


class SelectiveUpdateApplyWorker(QThread):
    """Фоновое скачивание ZIP во временную папку и выборочное применение файлов."""

    progress_updated = pyqtSignal(int, int, str)
    apply_finished = pyqtSignal(object, object)

    def __init__(
        self,
        zip_url: str,
        base_dir: Path,
        selected_files: List[str],
        remote_manifest: dict,
        entry_map: Dict[str, ThreeWayFileEntry],
    ):
        super().__init__(parent=None)
        self._zip_url = zip_url
        self._base_dir = Path(base_dir)
        self._selected_files = list(selected_files)
        self._remote_manifest = remote_manifest
        self._entry_map = dict(entry_map)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = download_and_apply_selective_update(
                zip_url=self._zip_url,
                base_dir=self._base_dir,
                selected_files=self._selected_files,
                remote_manifest=self._remote_manifest,
                entry_map=self._entry_map,
                progress_cb=lambda cur, tot, msg: self.progress_updated.emit(cur, tot, msg),
                cancel_check=self._cancel_event.is_set,
            )
            self.apply_finished.emit(result, None)
        except Exception as e:
            logger.error(f"[IntegrityWorker] Ошибка применения обновлений: {e}")
            self.apply_finished.emit(None, str(e))
