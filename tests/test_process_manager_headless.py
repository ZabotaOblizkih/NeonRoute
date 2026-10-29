"""
Unit tests for ProcessManager headless llama-server integration (Step 3).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.process_manager import ProcessManager, ServerState


def test_server_state_enum():
    expected_states = {
        "STOPPED", "STARTING", "READY", "SWITCHING_MODEL",
        "EVICTED_FOR_FORGE", "RESTORING", "ERROR"
    }
    actual_states = {s.value for s in ServerState}
    assert actual_states == expected_states


def test_process_manager_initial_state():
    pm = ProcessManager()
    assert pm.server_state == ServerState.STOPPED
    assert pm._llama_process is None
    assert pm._llama_log_file is None


def test_has_headless_server(monkeypatch):
    pm = ProcessManager()

    with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")):
        assert pm.has_headless_server() is True

    with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=None):
        assert pm.has_headless_server() is False


def test_kill_llama_server_instance_stopped():
    pm = ProcessManager()
    mock_proc = MagicMock()
    mock_proc.pid = 4321
    pm._llama_process = mock_proc

    mock_log = MagicMock()
    mock_log.closed = False
    pm._llama_log_file = mock_log

    with patch("subprocess.run") as mock_sub_run, \
         patch("psutil.process_iter", return_value=[]), \
         patch.object(pm, "is_port_open", return_value=False):
        pm._kill_llama_server_instance(for_forge_eviction=False)

    assert pm._llama_process is None
    assert pm._llama_log_file is None
    mock_log.close.assert_called_once()
    assert pm.server_state == ServerState.STOPPED

    # Verify taskkill was invoked for PID 4321
    called_cmds = [call.args[0] for call in mock_sub_run.call_args_list]
    assert any("taskkill" in cmd and "4321" in cmd for cmd in called_cmds)


def test_kill_llama_server_instance_for_forge_eviction():
    pm = ProcessManager()
    mock_proc = MagicMock()
    mock_proc.pid = 5678
    pm._llama_process = mock_proc

    with patch("subprocess.run"), \
         patch("psutil.process_iter", return_value=[]), \
         patch.object(pm, "is_port_open", return_value=False):
        pm._kill_llama_server_instance(for_forge_eviction=True)

    assert pm.server_state == ServerState.EVICTED_FOR_FORGE


def test_start_llama_server_sync_success(tmp_path):
    pm = ProcessManager()
    fake_exe = tmp_path / "llama-server.exe"
    fake_exe.touch()
    fake_model = tmp_path / "model.gguf"
    fake_model.touch()

    mock_popen = MagicMock()
    mock_popen.poll.return_value = None
    mock_popen.stdout.readline.return_value = ""

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    pm.lm_client = MagicMock()

    with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
         patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model), \
         patch("services.server_config.ServerConfigResolver.resolve_cuda_dir", return_value=tmp_path), \
         patch("services.server_config.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
         patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234, "default_context": 14336, "gpu_layers": 99}), \
         patch("subprocess.Popen", return_value=mock_popen), \
         patch("requests.get", return_value=mock_resp), \
         patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
         patch.object(pm, "_kill_llama_server_instance"):
        ok = pm.start_llama_server_sync("model.gguf")

    assert ok is True
    assert pm.server_state == ServerState.READY
    assert pm.lm_client._is_model_unloaded is False
    assert pm.lm_client._last_loaded_model_id == "model"


def test_start_llama_server_sync_missing_model():
    pm = ProcessManager()
    fake_exe = Path("C:/bin/llama-server.exe")

    with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
         patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=None), \
         patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
         patch.object(pm, "_kill_llama_server_instance"):
        ok = pm.start_llama_server_sync("non_existent_model")

    assert ok is False
    assert pm.server_state == ServerState.ERROR


def test_evict_llm_for_forge_sync():
    pm = ProcessManager()
    pm.lm_client = MagicMock()
    pm.lm_client._is_model_unloaded = False

    with patch.object(pm, "_kill_llama_server_instance") as mock_kill:
        res = pm.evict_llm_for_forge_sync()

    assert res is True
    mock_kill.assert_called_once_with(for_forge_eviction=True)
    assert pm.lm_client._is_model_unloaded is True


def test_is_lms_model_loaded_in_headless_mode():
    pm = ProcessManager()
    pm.lm_client = MagicMock()
    pm.lm_client._is_model_unloaded = False
    pm.lm_client._last_loaded_model_id = "qwen2.5-coder-7b-instruct-q5_k_m"

    with patch.object(pm, "has_headless_server", return_value=True):
        # When server is not READY -> False
        pm._server_state = ServerState.STOPPED
        assert pm.is_lms_model_loaded("qwen2.5-coder-7b-instruct-q5_k_m") is False

        # When server is READY -> True for matching stem / model
        pm._server_state = ServerState.READY
        assert pm.is_lms_model_loaded("qwen2.5-coder-7b-instruct-q5_k_m") is True
        assert pm.is_lms_model_loaded("qwen2.5-coder-7b-instruct-q5_k_m.gguf") is True
        assert pm.is_lms_model_loaded("completely_different_model") is False


def test_get_available_lms_models_fast_path(tmp_path):
    pm = ProcessManager()
    fake_model_path = tmp_path / "bartowski" / "L3-8B-Stheno-v3.2-GGUF" / "model.gguf"

    with patch("services.server_config.ServerConfigResolver.discover_gguf_models", return_value={"L3-8B-Stheno-v3.2-GGUF/model.gguf": fake_model_path}):
        models = pm.get_available_lms_models()

    assert "L3-8B-Stheno-v3.2-GGUF" in models
    assert "model" in models
    assert "L3-8B-Stheno-v3.2-GGUF/model.gguf" in models
