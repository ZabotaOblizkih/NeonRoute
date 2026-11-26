"""
Consolidated unit tests for KV Slot Lifecycle, Host RAM OOM Guards,
and llama-server CLI / crash diagnostics.
"""

import ctypes
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.layer_budgeter import LayerBudgeter
from services.lm_studio import LMStudioClient, LlamaServerStartupError
from services.process_manager import ProcessManager, ServerState


@pytest.fixture(autouse=True)
def fast_sleep_and_airgap(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture
def mock_kv_dir(tmp_path, monkeypatch):
    kv_dir = tmp_path / "kv_slots"
    kv_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("services.process_manager.KV_SLOTS_DIR", kv_dir)
    return kv_dir


def test_clear_kv_slot_cache_removes_dump_and_metadata(mock_kv_dir):
    pm = ProcessManager()
    slot_file = mock_kv_dir / "evicted_slot_0.bin"
    meta_file = mock_kv_dir / "slot_metadata.json"
    slot_file.write_bytes(b"dummy_slot_data")
    meta_file.write_text('{"dummy": true}', encoding="utf-8")

    pm.clear_kv_slot_cache()
    assert not slot_file.exists()
    assert not meta_file.exists()


def test_successful_kv_slot_save_and_restore_cycle(mock_kv_dir):
    pm = ProcessManager()
    pm._llama_process = MagicMock()
    pm._current_running_port = 1234
    pm._current_running_model_path = "C:/models/gemma-4-12b.gguf"
    pm._current_running_ctx = 14336
    pm._current_running_ngl = 99
    pm._current_total_layers = 48
    pm._current_running_ctk = "q8_0"
    pm._current_running_ctv = "q4_0"
    pm._current_running_fa = "on"

    old_dump = mock_kv_dir / "evicted_slot_0_old11111.bin"
    old_dump.write_bytes(b"old1")

    fake_mtime = 1700000000.0
    mock_save_resp = MagicMock(status_code=200)

    with patch("requests.post", return_value=mock_save_resp) as mock_post, \
         patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/gemma-4-12b.gguf")), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("os.path.getmtime", return_value=fake_mtime):
        assert pm._save_kv_slot_for_eviction() is True
        saved_filename = mock_post.call_args.kwargs["json"]["filename"]
        assert saved_filename.startswith("evicted_slot_0_")
        assert saved_filename != old_dump.name
        assert mock_post.call_args.kwargs["timeout"] >= 15.0

    meta_file = mock_kv_dir / "slot_metadata.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["gpu_layers"] == 48

    slot_file = mock_kv_dir / saved_filename
    slot_file.write_bytes(b"llama_slot_kv_binary_content")
    pm.clear_kv_slot_cache(keep_filename=saved_filename)
    assert slot_file.exists()
    assert not old_dump.exists()

    mock_restore_resp = MagicMock(status_code=200)
    with patch("requests.post", return_value=mock_restore_resp) as mock_post_restore:
        assert pm._restore_kv_slot_after_start(meta) is True
        assert mock_post_restore.call_args.args[0] == "http://127.0.0.1:1234/slots/0?action=restore"
        assert mock_post_restore.call_args.kwargs["json"]["filename"] == saved_filename

    assert not meta_file.exists()
    pm.clear_kv_slot_cache()
    assert not slot_file.exists()


@pytest.mark.parametrize("mismatched_key,mismatched_val", [
    ("model_path", "C:/models/qwen-2.5-7b.gguf"),
    ("model_mtime", 1800000000.0),
    ("context_length", 24576),
    ("gpu_layers", 47),
    ("kv_cache_type_v", "q8_0"),
    ("flash_attn", "off"),
])
def test_kv_slot_invalidation_on_parameter_change(mock_kv_dir, mismatched_key, mismatched_val):
    pm = ProcessManager()
    slot_file = mock_kv_dir / "evicted_slot_0.bin"
    meta_file = mock_kv_dir / "slot_metadata.json"
    slot_file.write_bytes(b"slot_binary")

    saved_meta = {
        "model_path": "C:/models/gemma-4-12b.gguf",
        "model_mtime": 1700000000.0,
        "context_length": 14336,
        "gpu_layers": 99,
        "kv_cache_type_k": "q8_0",
        "kv_cache_type_v": "q4_0",
        "flash_attn": "on",
    }
    meta_file.write_text(json.dumps(saved_meta), encoding="utf-8")

    expected_meta = dict(saved_meta)
    expected_meta[mismatched_key] = mismatched_val

    with patch("requests.post") as mock_post:
        assert pm._restore_kv_slot_after_start(expected_meta) is False
        mock_post.assert_not_called()

    assert not meta_file.exists()
    pm.clear_kv_slot_cache()
    assert not slot_file.exists()


def test_anomalous_dump_size_rejected_and_cache_purged(mock_kv_dir):
    pm = ProcessManager()
    huge_dump = mock_kv_dir / "evicted_slot_0_huge.bin"
    with open(huge_dump, "wb") as f:
        f.truncate(3 * 1024 * 1024 * 1024)

    meta_file = mock_kv_dir / "slot_metadata.json"
    meta = {
        "filename": "evicted_slot_0_huge.bin",
        "model_path": "C:/models/test.gguf",
        "model_mtime": 100.0,
        "context_length": 8192,
        "gpu_layers": 48,
        "kv_cache_type_k": "q8_0",
        "kv_cache_type_v": "q8_0",
        "flash_attn": "on",
    }
    meta_file.write_text(json.dumps(meta), encoding="utf-8")

    with patch("services.layer_budgeter.LayerBudgeter.estimate_kv_cache_mb", return_value=500.0), \
         patch("psutil.virtual_memory", return_value=MagicMock(available=4 * 1024 * 1024 * 1024)), \
         patch.object(pm, "erase_kv_slot_sync") as mock_erase, \
         patch("requests.post") as mock_post:
        assert pm._restore_kv_slot_after_start(meta) is False
        mock_post.assert_not_called()
        mock_erase.assert_called_once_with(slot_id=0)

    assert not meta_file.exists()
    pm.clear_kv_slot_cache()
    assert not huge_dump.exists()


def test_kv_slot_save_and_restore_fault_tolerance(mock_kv_dir):
    pm = ProcessManager()
    pm._llama_process = MagicMock()
    pm._llama_process.poll.return_value = None
    pm._current_running_port = 1234
    pm._current_running_model_path = "C:/models/test.gguf"

    with patch("requests.post", side_effect=requests.Timeout("Connection timeout")):
        assert pm._save_kv_slot_for_eviction() is False
        assert not (mock_kv_dir / "slot_metadata.json").exists()

    with patch.object(pm, "_save_kv_slot_for_eviction", return_value=False), \
         patch.object(pm, "_kill_llama_server_instance") as mock_kill:
        assert pm.evict_llm_for_forge_sync() is True
        mock_kill.assert_called_once_with(for_forge_eviction=True)

    slot_file = mock_kv_dir / "evicted_slot_0_test.bin"
    meta_file = mock_kv_dir / "slot_metadata.json"
    slot_file.write_bytes(b"data")
    meta = {
        "filename": "evicted_slot_0_test.bin",
        "model_path": "C:/models/test.gguf",
        "model_mtime": 100.0,
        "context_length": 8192,
        "kv_cache_type_k": "q8_0",
        "kv_cache_type_v": "q8_0",
        "gpu_layers": 48,
        "flash_attn": "on",
    }
    meta_file.write_text(json.dumps(meta), encoding="utf-8")

    def _post_router(url, *args, **kwargs):
        if "action=restore" in url:
            raise requests.exceptions.ReadTimeout("Restore timed out")
        return MagicMock(status_code=200)

    with patch("services.process_manager.ProcessManager.is_port_open", return_value=True), \
         patch("requests.post", side_effect=_post_router) as mock_post:
        assert pm._restore_kv_slot_after_start(meta) is False
        urls = [call[0][0] for call in mock_post.call_args_list]
        assert any("action=restore" in u for u in urls)
        assert any("action=erase" in u for u in urls)


@pytest.mark.parametrize("log_text", [
    "ggml_backend_cuda_host_buffer_type: failed to allocate 512.0 MB\nerror loading model: unable to allocate CUDA_Host buffer\n",
    "error loading model: bad allocation\n",
])
def test_host_ram_oom_classification(tmp_path, monkeypatch, log_text):
    pm = ProcessManager()
    log_file = tmp_path / "llama_server.log"
    log_file.write_text(log_text, encoding="utf-8")
    monkeypatch.setattr("services.process_manager.LOGS_DIR", tmp_path)

    err_msg, is_oom = pm._extract_llama_startup_error()
    assert is_oom is True
    assert pm.is_host_ram_oom_failure is True
    assert pm.is_vram_oom_failure is False
    assert "Host RAM Out of Memory" in err_msg


def test_host_ram_oom_retry_and_exhaustion(tmp_path, monkeypatch):
    pm = ProcessManager()
    LMStudioClient._instance = None
    lms_client = LMStudioClient()
    lms_client.set_process_manager(pm)

    model_file = tmp_path / "test_model.gguf"
    model_file.write_text("gguf_bytes")
    monkeypatch.setattr("services.server_config.ServerConfigResolver.resolve_model_path", lambda m: model_file)
    monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: False)
    monkeypatch.setattr(lms_client, "check_health", lambda: (False, ""))

    pm.is_host_ram_oom_failure = True
    pm.is_vram_oom_failure = False
    pm.last_error = "Host RAM Out of Memory: unable to allocate CUDA_Host buffer"

    start_calls = []

    def mock_start_ok_on_retry(model_identifier=None, context_length=None, gpu_layers=None, skip_kv_restore=False, **kwargs):
        start_calls.append({
            "gpu_layers": gpu_layers,
            "skip_kv_restore": skip_kv_restore,
            "force_no_pinned": getattr(pm, "_force_cuda_no_pinned", False),
        })
        return len(start_calls) > 1

    pm.start_llama_server_sync = mock_start_ok_on_retry
    assert lms_client.ensure_model_loaded(preferred_model="test_model", gpu_ratio="1.0") is True
    assert len(start_calls) == 2
    assert start_calls[1]["skip_kv_restore"] is True
    assert start_calls[1]["gpu_layers"] == start_calls[0]["gpu_layers"]
    assert start_calls[1]["force_no_pinned"] is True

    pm.start_llama_server_sync = lambda **kwargs: False
    with pytest.raises(LlamaServerStartupError) as exc_info:
        lms_client.ensure_model_loaded(preferred_model="test_model", gpu_ratio="1.0")
    assert "GGML_CUDA_NO_PINNED=1" in str(exc_info.value)


def test_connection_error_diagnostics_on_crashed_headless_server(monkeypatch):
    pm = ProcessManager()
    pm._server_state = ServerState.READY
    LMStudioClient._instance = None
    lms_client = LMStudioClient()
    lms_client.set_process_manager(pm)

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 3221225477
    pm._llama_process = mock_proc
    pm.last_error = "Access violation in tensor compute"

    monkeypatch.setattr(pm, "has_headless_server", lambda: True)
    monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
    lms_client._last_loaded_model_id = "test-model"
    lms_client._current_loaded_ctx = 16384
    monkeypatch.setattr(
        lms_client._persistent_session,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError("WinError 10054")),
    )

    with pytest.raises(ConnectionError) as exc_info:
        lms_client.chat_completion(model="test-model", context_length=8192, messages=[{"role": "user", "content": "hi"}])
    assert "Локальный процесс llama-server аварийно завершился (код 3221225477)" in str(exc_info.value)


def test_layer_budgeter_ram_not_clamped_by_small_pagefile(monkeypatch):
    class FakeMemoryStatus:
        def __init__(self, phys_mb, pagefile_mb):
            self.ullAvailPhys = int(phys_mb * 1024 * 1024)
            self.ullAvailPageFile = int(pagefile_mb * 1024 * 1024)

    fake_stat = FakeMemoryStatus(phys_mb=6000.0, pagefile_mb=800.0)

    def mock_global_mem_status(byref_stat):
        byref_stat._obj.ullAvailPhys = fake_stat.ullAvailPhys
        byref_stat._obj.ullAvailPageFile = fake_stat.ullAvailPageFile
        return 1

    monkeypatch.setattr(sys, "platform", "win32")
    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes.windll.kernel32, "GlobalMemoryStatusEx", mock_global_mem_status)
    assert LayerBudgeter.get_available_host_memory_mb() >= 6000.0


def test_process_manager_ubatch_and_asymmetric_kv_cli_flags(caplog):
    pm = ProcessManager()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"status": "ok"}

    for fa_enabled, exp_ctk, exp_ctv, exp_cache_arg in [
        (True, "q8_0", "q4_0", "q8_0_q4_0"),
        (False, "f16", "f16", "f16"),
    ]:
        captured_cmd = []

        def fake_popen(cmd, *args, **kwargs):
            captured_cmd.extend(cmd)
            mp = MagicMock()
            mp.poll.return_value = None
            mp.pid = 9999
            mp.stdout.readline.side_effect = ["HTTP server listening\n", ""]
            return mp

        with patch("services.process_manager.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")), \
             patch("services.process_manager.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/test.gguf")), \
             patch("services.process_manager.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.process_manager.ServerConfigResolver.resolve_cuda_dir", return_value=None), \
             patch("services.process_manager.ServerConfigResolver.load_ini", return_value={"ubatch_size": "256"}), \
             patch("utils.config_manager.ConfigManager.load_config", return_value={"kv_cache_type": "q8_0_q4_0", "flash_attention_enabled": fa_enabled}), \
             patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
             patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(40, True, "OK", {})) as mock_budget, \
             patch("subprocess.Popen", side_effect=fake_popen), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("requests.get", return_value=mock_resp):
            pm.start_llama_server_sync(model_identifier="test_model", context_length=8192)

        assert captured_cmd[captured_cmd.index("-b") + 1] == "512"
        assert captured_cmd[captured_cmd.index("-ub") + 1] == "256"
        assert captured_cmd[captured_cmd.index("-ctk") + 1] == exp_ctk
        assert captured_cmd[captured_cmd.index("-ctv") + 1] == exp_ctv
        assert mock_budget.call_args.kwargs.get("cache_type") == exp_cache_arg
        assert mock_budget.call_args.kwargs.get("ubatch_size") == 256
