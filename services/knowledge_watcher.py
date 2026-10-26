"""
Knowledge Watcher Satellite for NeonRoute.
Provides real-time file system monitoring and hot invalidation of the knowledge base.
Subject-neutral, CPU-friendly, debounced (2.0s), and non-blocking for UI.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Tuple, Set, Union
from PyQt6.QtCore import QObject, QFileSystemWatcher, QTimer, pyqtSignal

from services.knowledge_manager import KnowledgeManager, KnowledgeSyncWorker
from services.task_queue import get_task_queue, InferenceTask, PRIORITY_LOW

logger = logging.getLogger("NeonRoute.KnowledgeWatcher")

IGNORED_FILENAMES: Set[str] = {
    "registry.json",
    "registry.json.tmp",
    "search_index.json",
    "search_index.json.tmp",
}


class KnowledgeWatcher(QObject):
    """
    Сателлит для отслеживания каталога knowledge_base/ и динамической инвалидации индекса на лету.
    """
    sync_started = pyqtSignal()
    sync_finished = pyqtSignal(dict)
    sync_error = pyqtSignal(str)

    def __init__(
        self,
        knowledge_dir: Optional[Union[str, Path]] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        self.knowledge_dir: Path = (
            Path(knowledge_dir).resolve() if knowledge_dir else KnowledgeManager.get_knowledge_dir().resolve()
        )
        self._watcher = QFileSystemWatcher(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(2000)  # 2.0 секунды дебаунс
        self._timer.timeout.connect(self._on_debounce_timeout)

        self._file_snapshots: Dict[str, Tuple[float, int]] = {}
        self._is_syncing: bool = False
        self._sync_worker: Optional[KnowledgeSyncWorker] = None
        self._is_running: bool = False

    def start(self) -> None:
        """Запуск отслеживания файловой системы базы знаний."""
        if self._is_running:
            return

        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._file_snapshots = self._scan_files()

        # Добавляем каталог knowledge_base/
        self._watcher.addPath(str(self.knowledge_dir))

        # Добавляем существующие поддерживаемые файлы
        self._refresh_watched_files()

        self._watcher.directoryChanged.connect(self._on_fs_event)
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._is_running = True
        logger.info(f"[KnowledgeWatcher] Запущено наблюдение за каталогом: {self.knowledge_dir}")

    def stop(self) -> None:
        """Остановка отслеживания."""
        if not self._is_running:
            return

        self._timer.stop()
        watched = self._watcher.files() + self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)

        try:
            self._watcher.directoryChanged.disconnect(self._on_fs_event)
            self._watcher.fileChanged.disconnect(self._on_fs_event)
        except Exception:
            pass

        self._is_running = False
        logger.info("[KnowledgeWatcher] Наблюдение остановлено.")

    def _scan_files(self) -> Dict[str, Tuple[float, int]]:
        """Сканирует поддерживаемые документы и возвращает {filename: (mtime, size)}."""
        snapshots: Dict[str, Tuple[float, int]] = {}
        if not self.knowledge_dir.exists():
            return snapshots

        try:
            for item in self.knowledge_dir.iterdir():
                if not item.is_file():
                    continue
                if item.name in IGNORED_FILENAMES or item.name.startswith("."):
                    continue
                if item.suffix.lower() not in KnowledgeManager.SUPPORTED_EXTENSIONS:
                    continue
                try:
                    stat = item.stat()
                    snapshots[item.name] = (stat.st_mtime, stat.st_size)
                except OSError:
                    continue
        except Exception as e:
            logger.debug(f"[KnowledgeWatcher] Ошибка сканирования каталога {self.knowledge_dir}: {e}")

        return snapshots

    def _refresh_watched_files(self) -> None:
        """Обновляет список отслеживаемых путей файлов в QFileSystemWatcher."""
        if not self.knowledge_dir.exists():
            return

        currently_watched = set(self._watcher.files())
        desired_files: Set[str] = set()

        for fname in self._file_snapshots.keys():
            fpath = (self.knowledge_dir / fname).resolve()
            if fpath.exists():
                desired_files.add(str(fpath))

        to_add = desired_files - currently_watched
        if to_add:
            self._watcher.addPaths(list(to_add))

        to_remove = currently_watched - desired_files
        if to_remove:
            self._watcher.removePaths(list(to_remove))

    def _on_fs_event(self, path: str) -> None:
        """Обработчик событий изменения файла или каталога."""
        if self._is_syncing:
            return

        # Игнорируем служебные файлы индекса и реестра
        p_name = Path(path).name
        if p_name in IGNORED_FILENAMES or p_name.endswith(".tmp"):
            return

        # Проверяем, изменился ли снимок реальных документов
        current_snapshots = self._scan_files()
        if current_snapshots == self._file_snapshots:
            return

        # Перезапускаем дебаунс-таймер на 2 секунды
        self._timer.start(2000)

    def _on_debounce_timeout(self) -> None:
        """Таймаут дебаунса: запуск синхронизации."""
        self._is_syncing = True
        logger.info("[KnowledgeWatcher] Обнаружено изменение базы знаний, запущен scan_and_sync")
        self.sync_started.emit()

        self._file_snapshots = self._scan_files()
        self._refresh_watched_files()

        self._dispatch_sync()

    def _dispatch_sync(self) -> None:
        """
        Отправляет вызов KnowledgeManager.scan_and_sync() в TaskQueue с PRIORITY_LOW = 20
        либо выполняет в фоновом потоке QThread, не блокируя UI.
        """
        tq = get_task_queue()
        if tq is not None and getattr(tq, "_running", False) and tq.isRunning():
            def _task_fn():
                try:
                    result = KnowledgeManager.scan_and_sync(custom_dir=self.knowledge_dir)
                    return result
                finally:
                    self._is_syncing = False

            task = InferenceTask(
                name="KnowledgeWatcherSync",
                task_type="knowledge_sync",
                func=_task_fn,
                priority=PRIORITY_LOW
            )
            tq.enqueue(task)
        else:
            # Запуск в фоновом QThread
            self._sync_worker = KnowledgeSyncWorker(self)

            def _on_finished(result: dict):
                self._is_syncing = False
                self.sync_finished.emit(result)

            def _on_error(err: str):
                self._is_syncing = False
                self.sync_error.emit(err)
                logger.error(f"[KnowledgeWatcher] Ошибка фоновой синхронизации: {err}")

            self._sync_worker.sync_finished.connect(_on_finished)
            self._sync_worker.sync_error.connect(_on_error)
            self._sync_worker.start()
