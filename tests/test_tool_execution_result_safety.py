import pytest
from unittest.mock import MagicMock
from pathlib import Path
from services.tool_engine import ToolResult
from ui.controllers.chat_flow_controller import ChatFlowControllerMixin
from ui.controllers.engineer_controller import EngineerControllerMixin
from ui.workers.tool_execution_worker import ToolExecutionWorker


class DummyChatHost(ChatFlowControllerMixin):
    def __init__(self):
        self.chat_history = []
        self._is_post_tool_synthesis = False
        self._synthesis_first_chunk = False
        self._tool_executed_this_turn = False
        self._turn_bubble = MagicMock()
        self._enqueue_llm_task = MagicMock()
        self._remove_typing_loader = MagicMock()
        self._force_reset_busy_state = MagicMock()
        self._execute_workspace_script = MagicMock()
        self._update_message_attachment = MagicMock()


class DummyEngineerHost(EngineerControllerMixin):
    def __init__(self):
        self.chat_history = []
        self._is_post_tool_synthesis = False
        self._synthesis_first_chunk = False
        self._tool_executed_this_turn = False
        self._turn_bubble = MagicMock()
        self._enqueue_llm_task = MagicMock()
        self._remove_typing_loader = MagicMock()
        self._force_reset_busy_state = MagicMock()
        self._execute_workspace_script = MagicMock()
        self._update_message_attachment = MagicMock()


def test_tool_execution_worker_signal_type():
    worker = ToolExecutionWorker(tool_name="test")
    # Verify signal signature accepts generic object/dict/ToolResult
    # pyqtSignal(object) can emit any object without TypeError
    assert hasattr(worker, "finished")


def test_on_tool_execution_finished_dict_handling():
    host = DummyChatHost()
    data = {
        "results": [
            {"tool_name": "write_file", "result": "File created: test.py", "success": True}
        ],
        "overall_success": True,
        "error": "",
        "tool_name": "write_file",
        "artifacts": []
    }

    host._on_tool_execution_finished(data)

    assert host._tool_executed_this_turn is True
    assert host._is_post_tool_synthesis is True
    assert host._synthesis_first_chunk is True
    assert len(host.chat_history) == 1
    assert "[write_file]: File created: test.py" in host.chat_history[0]["content"]
    host._remove_typing_loader.assert_called_once()
    host._enqueue_llm_task.assert_called_once_with(is_roleplay=False)
    host._force_reset_busy_state.assert_called_once()


def test_on_tool_execution_finished_dict_error_handling():
    host = DummyChatHost()
    data = {
        "results": [
            {"tool_name": "error", "result": "Критическая ошибка выполнения инструмента: disk error", "success": False}
        ],
        "overall_success": False,
        "error": "disk error",
        "tool_name": "error"
    }

    host._on_tool_execution_finished(data)

    assert host._tool_executed_this_turn is True
    assert len(host.chat_history) == 1
    assert "Критическая ошибка" in host.chat_history[0]["content"]
    host._force_reset_busy_state.assert_called_once()


def test_on_tool_execution_finished_tool_result_handling():
    host = DummyChatHost()
    res = ToolResult(
        tool_name="write_file",
        success=True,
        output="[write_file]: Successfully written 120 bytes",
        artifact_path=None
    )

    # Passing ToolResult directly should not raise AttributeError: 'dict' object has no attribute 'success'
    # or AttributeError: 'ToolResult' object has no attribute 'get'
    host._on_tool_execution_finished(res)

    assert host._tool_executed_this_turn is True
    assert host._is_post_tool_synthesis is True
    assert len(host.chat_history) == 1
    assert "Successfully written 120 bytes" in host.chat_history[0]["content"]
    host._force_reset_busy_state.assert_called_once()


def test_on_tool_execution_finished_tool_result_error_handling():
    host = DummyChatHost()
    res = ToolResult(
        tool_name="run_script",
        success=False,
        output="Script failed with exit code 1",
        stderr="SyntaxError: invalid syntax"
    )

    host._on_tool_execution_finished(res)

    assert host._tool_executed_this_turn is True
    assert len(host.chat_history) == 1
    assert "SyntaxError: invalid syntax" in host.chat_history[0]["content"]
    host._force_reset_busy_state.assert_called_once()


def test_on_tool_execution_finished_empty_data():
    host = DummyChatHost()
    host._on_tool_execution_finished(None)
    host._force_reset_busy_state.assert_called_once()


def test_on_tool_execution_finished_exception_guarantees_finally_unfreeze():
    host = DummyChatHost()
    # Force an exception during processing
    host.chat_history = None  # appending to None will raise AttributeError

    host._on_tool_execution_finished({"overall_success": True, "results": []})
    # Despite the exception, _force_reset_busy_state must be called in finally block
    host._force_reset_busy_state.assert_called_once()


def test_engineer_controller_safe_unpacking_both_types():
    host = DummyEngineerHost()

    # 1. Test dict
    host._on_tool_execution_finished({
        "results": [{"tool_name": "find_files", "result": "found test.py", "success": True}],
        "overall_success": True
    })
    assert len(host.chat_history) == 1
    host._force_reset_busy_state.assert_called_once()

    # 2. Test ToolResult
    res = ToolResult(tool_name="read_file", success=True, output="contents of file")
    host._on_tool_execution_finished(res)
    assert len(host.chat_history) == 2
    assert host._force_reset_busy_state.call_count == 2
