"""
Dependency injection and singletons for NeonRoute Mobile Server.
"""

import logging
from typing import Dict, Any, List, Optional

from utils.health_checker import HealthChecker
from utils.system_monitor import SystemMonitor
from services.session_manager import SessionManager
from services.lm_studio import LMStudioClient
from services.mode_coordinator import ModeCoordinator, AppMode
from services.forge_client import ForgeClient
from services.process_manager import get_or_create_process_manager
from services.task_queue import TaskQueueWorker, get_task_queue
from services.vram_arbiter import VRAMArbiter
from services.art_director.director import ArtDirector

logger = logging.getLogger("NeonRoute.MobileServer.Dependencies")


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


# Singleton instances (reuse MainWindow / VRAMArbiter singletons when running in-process)
_health_checker = HealthChecker()
_system_monitor = SystemMonitor()
_session_manager = SessionManager.get_instance()
_mode_coordinator = HeadlessModeCoordinator()

_vram_arbiter = VRAMArbiter.get_instance()
_lm_studio_client = _vram_arbiter.lm_client if _vram_arbiter.lm_client is not None else LMStudioClient()
_forge_client = _vram_arbiter.forge_client if _vram_arbiter.forge_client is not None else ForgeClient.get_instance()
if _vram_arbiter.lm_client is None or _vram_arbiter.forge_client is None:
    _vram_arbiter.set_clients(_lm_studio_client, _forge_client)

_process_manager = get_or_create_process_manager()
if getattr(_lm_studio_client, "process_manager", None) is None:
    _lm_studio_client.set_process_manager(_process_manager)
if getattr(_process_manager, "lm_client", None) is None:
    _process_manager.lm_client = _lm_studio_client

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
