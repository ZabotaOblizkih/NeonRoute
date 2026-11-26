"""UI/Controller tests for HardwareEngineMixin and KV cache cleanup on session switch/shutdown."""

from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication, QWidget, QMessageBox, QFileDialog

from services.sandbox_manager import SandboxManager
from services.vram_arbiter import VRAMArbiter
from ui.controllers.hardware_engine import HardwareEngineMixin
from ui.controllers.session_controller import SessionControllerMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class DummyHardwareHost(QWidget, HardwareEngineMixin):
    def __init__(self):
        super().__init__()
        self._status_bar_mock = MagicMock()
        self.process_manager = MagicMock()
        self.forge_client = MagicMock()
        self.task_queue = MagicMock()
        self.pill_forge = MagicMock()
        self._reload_active_lms_model_async = MagicMock()
        self._cancel_lms_reload = MagicMock()
        self._active_tasks = {}

    def statusBar(self):
        return self._status_bar_mock


def test_prompt_locate_forge_reentrancy_guard(qapp):
    host = DummyHardwareHost()
    host._is_locating_forge = True
    try:
        with patch.object(QMessageBox, "exec") as mock_msg_box, \
             patch.object(QFileDialog, "getExistingDirectory") as mock_file_dialog:
            assert host.prompt_locate_forge() is False
            mock_msg_box.assert_not_called()
            mock_file_dialog.assert_not_called()
    finally:
        host.deleteLater()


def test_scene_art_burst_requests_never_trigger_vram_release(qapp):
    host = DummyHardwareHost()
    host.process_manager.is_forge_running.return_value = False
    host.forge_client.is_server_ready.return_value = False
    host.pill_forge.is_online = False
    try:
        with patch.object(VRAMArbiter, "release_llm_for_diffusion") as mock_release, \
             patch("services.forge_client.ForgeClient.get_instance", return_value=host.forge_client):
            for i in range(5):
                host._on_scene_art_requested(text=f"Быстрое сообщение диалога #{i}")
            assert mock_release.call_count == 0
            assert host.task_queue.enqueue.call_count == 0
            assert host._reload_active_lms_model_async.call_count == 0
    finally:
        host.deleteLater()


def test_clear_kv_slot_cache_invoked_on_session_switch_and_shutdown(qapp):
    class DummyWindow(QWidget, SessionControllerMixin):
        def __init__(self):
            super().__init__()
            self.current_session = None
            self.chat_history = []
            self._is_switching_session = False
            self.process_manager = MagicMock()
            self.app_config = {}

        def _clear_chat_widgets(self):
            pass

        def _remove_greeting_cycler_chip(self):
            pass

    win = DummyWindow()
    try:
        with patch("services.session_manager.SessionManager.create_session", side_effect=RuntimeError("stop")):
            try:
                win._create_new_session()
            except RuntimeError:
                pass
        win.process_manager.clear_kv_slot_cache.assert_called_once()
        win.process_manager.clear_kv_slot_cache.reset_mock()

        with patch("services.session_manager.SessionManager.load_session", return_value=None):
            win._switch_to_session("test-session-id")
        win.process_manager.clear_kv_slot_cache.assert_called_once()
    finally:
        SandboxManager.set_active_session_id(None)
        win.deleteLater()

    from ui.workers.shutdown_worker import ShutdownWorker
    mock_main_win = MagicMock()
    mock_pm = MagicMock()
    mock_main_win.process_manager = mock_pm
    worker = ShutdownWorker(mock_main_win)
    with patch.object(mock_main_win, "_save_current_session"), \
         patch("utils.config_manager.ConfigManager.save_config"), \
         patch("services.memory_db.memory_db.close"):
        worker.run()
    mock_pm.clear_kv_slot_cache.assert_called_once()
    mock_pm.shutdown_all.assert_called_once()
