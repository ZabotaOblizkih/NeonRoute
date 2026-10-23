"""
Unit tests for UI Unfreeze on Task Failure, Abort, and Stop Clicked.
Guarantees that active service/background tasks in _active_tasks do not prevent
the UI from unfreezing when interactive tasks fail or are aborted.
"""

import pytest
from unittest.mock import MagicMock
from ui.controllers.chat_flow_controller import ChatFlowControllerMixin
from ui.main_window import MainWindow


class DummyHost(ChatFlowControllerMixin):
    def __init__(self):
        self._active_tasks = {}
        self.is_busy = True
        self.input_box = MagicMock()
        self.input_box.is_busy = True
        self.input_box.text_input = MagicMock()
        self.btn_send = MagicMock()
        self.typing_indicator = MagicMock()
        self._remove_typing_loader = MagicMock()
        self.statusBar = MagicMock()


def test_force_reset_busy_state_full_unfreeze():
    host = DummyHost()
    host._active_tasks["task_bg_1"] = MagicMock()
    host._active_tasks["task_failed_1"] = MagicMock()

    host._force_reset_busy_state("task_failed_1")

    # 1. Target task popped from _active_tasks
    assert "task_failed_1" not in host._active_tasks
    assert "task_bg_1" in host._active_tasks

    # 2. Host is no longer busy
    assert host.is_busy is False

    # 3. Input box unlocked
    host.input_box.set_busy.assert_called_with(False)
    host.input_box.setEnabled.assert_called_with(True)
    host.input_box.text_input.setReadOnly.assert_called_with(False)
    host.input_box.text_input.setFocus.assert_called_once()

    # 4. Send button enabled
    host.btn_send.setEnabled.assert_called_with(True)

    # 5. Typing indicator stopped
    host.typing_indicator.stop.assert_called_once()
    host.remove_loader_called = host._remove_typing_loader.called
    assert host.remove_loader_called is True


def test_on_task_failed_unfreezes_ui_with_lingering_tasks():
    # Verify MainWindow._on_task_failed unfreezes even when _active_tasks is not empty
    win = MagicMock(spec=MainWindow)
    win._active_tasks = {"bg_service_task": MagicMock(), "failed_task": MagicMock()}
    win.is_busy = True
    win.input_box = MagicMock()
    win.header_controller = MagicMock()
    win._update_header_for_current_state = MagicMock()
    win._append_message = MagicMock()
    win.statusBar = MagicMock()

    # Call MainWindow._on_task_failed as unbound method
    MainWindow._on_task_failed(win, "failed_task", "Network timeout or socket error")

    # _force_reset_busy_state should be called with "failed_task"
    win._force_reset_busy_state.assert_called_once_with("failed_task")


def test_on_task_aborted_unfreezes_ui_with_lingering_tasks():
    # Verify MainWindow._on_task_aborted unfreezes even when _active_tasks is not empty
    win = MagicMock(spec=MainWindow)
    win._active_tasks = {"bg_service_task": MagicMock(), "aborted_task": MagicMock()}
    win.is_busy = True
    win.input_box = MagicMock()
    win.header_controller = MagicMock()
    win._update_header_for_current_state = MagicMock()
    win._append_message = MagicMock()
    win.statusBar = MagicMock()

    # Call MainWindow._on_task_aborted as unbound method
    MainWindow._on_task_aborted(win, "aborted_task")

    # _force_reset_busy_state should be called with "aborted_task"
    win._force_reset_busy_state.assert_called_once_with("aborted_task")


def test_on_stop_clicked_unfreezes_ui():
    win = MagicMock(spec=MainWindow)
    win.statusBar = MagicMock()
    win.task_queue = MagicMock()
    win.abort_generation = MagicMock()

    # Call MainWindow._on_stop_clicked as unbound method
    MainWindow._on_stop_clicked(win)

    win.task_queue.abort_current.assert_called_once()
    win.abort_generation.assert_called_once()
    win._force_reset_busy_state.assert_called_once()
