"""
Dependency injection and singletons for NeonRoute Mobile Server.
"""

import queue
import threading
import logging
from typing import Iterator, Dict, Any, List, Optional

# 1. HealthChecker integration and aliasing
import utils.health_checker
if not hasattr(utils.health_checker, "HealthChecker"):
    utils.health_checker.HealthChecker = utils.health_checker.HealthCheckWorker
from utils.health_checker import HealthChecker

# 2. SystemMonitor
from utils.system_monitor import SystemMonitor

# 3. SessionManager
from services.session_manager import SessionManager

# 4. LMStudioClient & stream_chat_completion extension
from services.lm_studio import LMStudioClient

# 5. ModeCoordinator & HeadlessMainWindow
from services.mode_coordinator import ModeCoordinator, AppMode

# 6. Forge, VRAM Arbiter, TaskQueue and ArtDirector
from services.forge_client import ForgeClient
from services.task_queue import TaskQueueWorker, get_task_queue, PRIORITY_NORMAL, PRIORITY_HIGH, InferenceTask
from services.vram_arbiter import VRAMArbiter
from services.art_director.director import ArtDirector

logger = logging.getLogger("NeonRoute.MobileServer.Dependencies")


def _stream_chat_completion(
    self: LMStudioClient,
    messages: List[Dict[str, Any]],
    **kwargs
) -> Iterator[str]:
    """
    Generator yielding token strings by invoking chat_completion
    with chunk_callback in a background thread.
    """
    q: queue.Queue = queue.Queue()
    _SENTINEL = object()

    def worker():
        try:
            self.chat_completion(
                messages=messages,
                chunk_callback=lambda tok: q.put(tok),
                stream=True,
                emit_signal=False,
                **kwargs
            )
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(_SENTINEL)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        if isinstance(item, Exception):
            raise item
        yield item


if not hasattr(LMStudioClient, "stream_chat_completion"):
    LMStudioClient.stream_chat_completion = _stream_chat_completion


class HeadlessMainWindow:
    """Lightweight headless mock of MainWindow for ModeCoordinator without GUI components."""
    def __init__(self):
        self.current_mode = "assistant"
        self._engineering_mode_active = False
        self.current_session: Optional[Dict[str, Any]] = None
        self.chat_history: List[Dict[str, Any]] = []
        self._active_tasks: Dict[str, Any] = {}
        self.lm_client = None
        self._is_switching_session = False
        self.active_character = None
        self.lm_combo = None
        self._turn_bubble = None
        self._current_streaming_widget = None
        self._stream_token_buffer = ""

    def abort_generation(self):
        pass

    def _update_header_for_current_state(self):
        pass


class HeadlessModeCoordinator(ModeCoordinator):
    """
    Headless ModeCoordinator implementation.
    Safely switches active operating mode without GUI rebuild.
    """
    def __init__(self):
        self._headless_mw = HeadlessMainWindow()
        super().__init__(self._headless_mw)

    def _boot_target_mode(self, mode: AppMode, session_data: Optional[dict] = None):
        """Headless boot phase: switches mode state without GUI widget manipulation."""
        self.mw._engineering_mode_active = (mode == AppMode.ENGINEER)

    def _mount_session(self, target_mode: AppMode, session_data: dict, is_new: bool = False):
        """Headless mount phase: updates current_session without GUI sidebar/chat view manipulation."""
        self.mw.current_session = session_data
        self.mw.chat_history = list(session_data.get("chat_history", []))


# Singleton instances
_health_checker = HealthChecker()
_system_monitor = SystemMonitor()
_session_manager = SessionManager.get_instance()
_lm_studio_client = LMStudioClient()
_mode_coordinator = HeadlessModeCoordinator()

_forge_client = ForgeClient()
_vram_arbiter = VRAMArbiter.get_instance()
_vram_arbiter.set_clients(_lm_studio_client, _forge_client)

_task_queue = get_task_queue()
if _task_queue is None:
    _task_queue = TaskQueueWorker()
    if not _task_queue.isRunning():
        _task_queue.start()

_art_director = ArtDirector()


def get_health_checker() -> HealthChecker:
    return _health_checker


def get_system_monitor() -> SystemMonitor:
    return _system_monitor


def get_session_manager() -> SessionManager:
    return _session_manager


def get_lm_studio_client() -> LMStudioClient:
    return _lm_studio_client


def get_mode_coordinator() -> HeadlessModeCoordinator:
    return _mode_coordinator


def get_forge_client() -> ForgeClient:
    return _forge_client


def get_vram_arbiter() -> VRAMArbiter:
    return _vram_arbiter


def get_task_queue_worker() -> TaskQueueWorker:
    global _task_queue
    if _task_queue is None or not _task_queue.isRunning():
        _task_queue = get_task_queue() or TaskQueueWorker()
        if not _task_queue.isRunning():
            _task_queue.start()
    return _task_queue


def get_art_director() -> ArtDirector:
    return _art_director

