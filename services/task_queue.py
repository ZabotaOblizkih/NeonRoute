"""
Strict Sequential Priority Task Queue for NeonRoute
Enforces hardware limits (8 GB VRAM) by ensuring only ONE inference
task runs at a time. Supports priority scheduling and immediate abort/cancellation.
"""

import uuid
import time
import logging
import threading
from queue import PriorityQueue, Empty
from typing import Callable, Any, Optional
from PyQt6.QtCore import QObject, pyqtSignal, QThread

from services.vram_arbiter import VRAMArbiter


logger = logging.getLogger("NeonRoute.TaskQueue")

# Приоритеты задач (меньшее число = выше приоритет исполнения)
PRIORITY_HIGH = 0     # Интерактивные ответы пользователю (LLM диалог)
PRIORITY_NORMAL = 10  # Генерация артов (Forge txt2img), TTS, файлы
PRIORITY_LOW = 20     # Фоновые сервисные задачи (автоэкстракция памяти)

_GLOBAL_TASK_QUEUE: Optional["TaskQueueWorker"] = None


def get_task_queue() -> Optional["TaskQueueWorker"]:
    """Возвращает глобальный рабочий инстанс очереди задач."""
    global _GLOBAL_TASK_QUEUE
    return _GLOBAL_TASK_QUEUE


class InferenceTask:
    """Represents an atomic inference or generation task with priority."""
    def __init__(
        self,
        name: str,
        task_type: str,
        func: Callable[..., Any],
        args: tuple = (),
        kwargs: Optional[dict] = None,
        abort_fn: Optional[Callable[[], None]] = None,
        metadata: Optional[dict] = None,
        priority: Optional[int] = None
    ):
        self.id = str(uuid.uuid4())
        self.name = name
        self.task_type = task_type  # 'llm', 'image', 'tts', 'file', 'memory'
        self.func = func
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.abort_fn = abort_fn
        self.metadata = metadata or {}
        self.keep_llm_unloaded: bool = bool(self.metadata.get('keep_llm_unloaded', False))
        self.is_aborted = threading.Event()
        self.created_at = time.perf_counter()

        # Автоматическое определение приоритета, если не задан явно
        if priority is not None:
            self.priority = priority
        elif task_type == "llm":
            self.priority = PRIORITY_HIGH
        elif task_type in ("image", "scene_art", "scene_background", "tts", "file", "translation"):
            self.priority = PRIORITY_NORMAL
        else:
            self.priority = PRIORITY_LOW

    def abort(self):
        """Signals task to abort and triggers abort callback."""
        self.is_aborted.set()
        if self.abort_fn:
            try:
                self.abort_fn()
            except Exception as e:
                logger.error(f"Error executing abort callback for task {self.id}: {e}")


class TaskQueueWorker(QThread):
    """
    Background worker thread processing tasks sequentially based on priority.
    Prevents VRAM overflow on 8GB systems and eliminates concurrent socket collisions.
    """
    task_started = pyqtSignal(str, str, str)       # task_id, name, task_type
    task_finished = pyqtSignal(str, object)        # task_id, result
    task_failed = pyqtSignal(str, str)             # task_id, error_message
    task_aborted = pyqtSignal(str)                 # task_id
    queue_empty = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._queue: PriorityQueue = PriorityQueue()
        self._current_task: Optional[InferenceTask] = None
        self._lock = threading.RLock()
        self._running = True
        self._seq_counter = 0

        global _GLOBAL_TASK_QUEUE
        _GLOBAL_TASK_QUEUE = self

    def enqueue(self, task: InferenceTask):
        """Add a new task to the prioritized sequential queue."""
        if task.task_type in ("llm", "image", "scene_art", "scene_background") and not (task.metadata or {}).get("is_llm_reload"):
            try:
                VRAMArbiter.get_instance().cancel_llm_reload(reason=f"new_task:{task.task_type}")
            except Exception:
                pass
        with self._lock:
            self._seq_counter += 1
            seq = self._seq_counter
        self._queue.put((task.priority, seq, task))
        if task.priority < PRIORITY_LOW:
            logger.info(f"[TaskQueue] Enqueued task: {task.name} ({task.task_type}, prio={task.priority}) [ID: {task.id}]")
        else:
            logger.debug(f"[TaskQueue] Enqueued task: {task.name} ({task.task_type}, prio={task.priority}) [ID: {task.id}]")

    @property
    def current_task(self) -> Optional[InferenceTask]:
        return self._current_task

    def abort_current(self):
        """Immediately aborts currently running task."""
        with self._lock:
            if self._current_task and not self._current_task.is_aborted.is_set():
                logger.warning(f"Aborting active task: {self._current_task.name} [{self._current_task.id}]")
                self._current_task.abort()

    def abort_tasks_by_type(self, task_type: str):
        """Вытесняющая отмена активной и запланированных задач указанного типа."""
        with self._lock:
            # 1. Если текущая выполняемая задача совпадает по типу — прерываем инференс
            if self._current_task and getattr(self._current_task, "task_type", None) == task_type:
                logger.info(f"[TaskQueue.Preemption] Отмена устаревшей задачи '{self._current_task.name}' ({task_type})")
                self.abort_current()
            # 2. Очищаем очередь от задач этого типа
            temp_list = []
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    self._queue.task_done()
                    task_obj = item[2] if (isinstance(item, tuple) and len(item) == 3) else item
                    t_type = getattr(task_obj, "task_type", None)
                    if t_type == task_type:
                        t_name = getattr(task_obj, "name", str(task_obj))
                        if hasattr(task_obj, "abort"):
                            task_obj.abort()
                        logger.info(f"[TaskQueue.Preemption] Удалена из очереди задача '{t_name}' ({task_type})")
                        continue
                    temp_list.append(item)
                except Exception:
                    break
            for t in temp_list:
                self._queue.put(t)

    def has_pending_tasks(self, task_types: tuple[str, ...]) -> bool:
        """Потокобезопасно проверяет наличие ожидающих задач указанных типов в очереди."""
        with self._lock:
            for item in list(self._queue.queue):
                if isinstance(item, tuple) and len(item) == 3:
                    task = item[2]
                    if task is not None and not task.is_aborted.is_set() and task.task_type in task_types:
                        return True
        return False

    def clear_queue(self):
        """Clears all pending tasks from the queue."""
        cleared_count = 0
        while not self._queue.empty():
            try:
                _, _, task = self._queue.get_nowait()
                if task:
                    task.abort()
                self._queue.task_done()
                cleared_count += 1
            except Empty:
                break
        logger.info(f"Cleared {cleared_count} pending tasks from queue.")

    def run(self):
        """Main queue execution loop."""
        try:
            while self._running:
                try:
                    item = self._queue.get(timeout=0.5)
                except Empty:
                    continue

                if item is None or (isinstance(item, tuple) and item[2] is None):
                    self._queue.task_done()
                    break

                _, _, task = item

                with self._lock:
                    self._current_task = task

                if task.is_aborted.is_set():
                    self.task_aborted.emit(task.id)
                    self._queue.task_done()
                    with self._lock:
                        self._current_task = None
                    continue

                queue_wait_ms = (time.perf_counter() - getattr(task, "created_at", time.perf_counter())) * 1000
                log_as_info = (task.priority < PRIORITY_LOW) or (queue_wait_ms > 200.0)

                start_msg = f"[TaskQueue] Запуск: '{task.name}' ({task.task_type}, wait={queue_wait_ms:.1f}мс, prio={task.priority}) [ID: {task.id}]"
                if log_as_info:
                    logger.info(start_msg)
                else:
                    logger.debug(start_msg)

                self.task_started.emit(task.id, task.name, task.task_type)

                t_exec_start = time.perf_counter()
                try:
                    # Детерминированная координация VRAM перед инференсом
                    VRAMArbiter.get_instance().prepare_for_task(task.task_type, metadata=task.metadata)
                    result = task.func(*task.args, **task.kwargs)
                    exec_duration_ms = (time.perf_counter() - t_exec_start) * 1000
                    if task.is_aborted.is_set():
                        logger.info(f"[TaskQueue] Task completed but was aborted: {task.name} ({exec_duration_ms:.1f}мс)")
                        self.task_aborted.emit(task.id)
                    else:
                        if isinstance(result, dict) and result.get("success") is False:
                            err_summary = result.get("summary") or result.get("stderr") or "ошибка выполнения"
                            logger.warning(f"[TaskQueue] Задача завершена с ошибкой ({exec_duration_ms:.1f}мс): {task.name} ({str(err_summary)[:100]})")
                        else:
                            finish_msg = f"[TaskQueue] Задача '{task.name}' успешно выполнена за {exec_duration_ms:.1f}мс"
                            if log_as_info:
                                logger.info(finish_msg)
                            else:
                                logger.debug(finish_msg)
                        self.task_finished.emit(task.id, result)
                except Exception as e:
                    exec_duration_ms = (time.perf_counter() - t_exec_start) * 1000
                    if task.is_aborted.is_set() or isinstance(e, InterruptedError):
                        logger.info(f"[TaskQueue] Task aborted with exception (expected, {exec_duration_ms:.1f}мс): {e}")
                        self.task_aborted.emit(task.id)
                    else:
                        err_str = str(e)
                        if "Вы сменили модель, начните новый диалог, чтобы продолжить" in err_str:
                            logger.warning(f"[TaskQueue] Task stopped (model switched, {exec_duration_ms:.1f}мс): {task.name} - {err_str}")
                        else:
                            logger.error(f"[TaskQueue] Task failed ({exec_duration_ms:.1f}мс): {task.name} - Error: {e}", exc_info=True)
                        self.task_failed.emit(task.id, err_str)
                finally:
                    with self._lock:
                        self._current_task = None
                    self._queue.task_done()
                    if self._queue.empty():
                        self.queue_empty.emit()
        except Exception as e:
            logger.error(f"TaskQueueWorker encountered fatal error: {e}", exc_info=True)
            task_id = self._current_task.id if self._current_task else ""
            self.task_failed.emit(task_id, str(e))


    def stop(self):
        """Stops the worker thread cleanly."""
        self._running = False
        try:
            VRAMArbiter.get_instance().cancel_llm_reload(reason="task_queue_stop")
        except Exception:
            pass
        self.abort_current()
        self.clear_queue()
        with self._lock:
            self._seq_counter += 1
            seq = self._seq_counter
        self._queue.put((9999, seq, None))
        self.wait(1000)
