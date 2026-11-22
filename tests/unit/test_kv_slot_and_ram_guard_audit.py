"""
Unit tests for KV Slot Lifecycle, RAM Budgeting, and llama-server Error Diagnostics.

Covers:
1. Saving KV slot to a unique file recorded in slot_metadata.json and post-stop purge of old dumps.
2. Rejection of anomalous KV slot dump sizes and invocation of action=erase on ReadTimeout / restore failure.
3. Classification of 'unable to allocate CUDA_Host buffer' as is_host_ram_oom_failure and retry with GGML_CUDA_NO_PINNED=1 without reducing NGL.
4. Accurate crash diagnostics on ConnectionError when headless llama-server exits abnormally (and normal handling during Forge eviction).
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.layer_budgeter import LayerBudgeter
from services.lm_studio import LMStudioClient
from services.process_manager import ProcessManager, ServerState


@pytest.fixture
def mock_kv_dir(tmp_path, monkeypatch):
    """Isolates KV_SLOTS_DIR into a temporary directory."""
    kv_dir = tmp_path / "kv_slots"
    kv_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("services.process_manager.KV_SLOTS_DIR", kv_dir)
    return kv_dir


# -------------------------------------------------------------------------
# 1. Unique slot filenames and post-stop cleanup
# -------------------------------------------------------------------------
def test_kv_slot_unique_filename_and_post_stop_cleanup(mock_kv_dir):
    """Verifies that each save generates a unique filename, saves it to metadata, and purges older dumps post-stop."""
    pm = ProcessManager()
    pm._llama_process = MagicMock()
    pm._current_running_port = 1234
    pm._current_running_model_path = "C:/models/test_model.gguf"
    pm._current_running_ctx = 8192
    pm._current_running_ngl = 48
    pm._current_total_layers = 48

    # Create dummy previous dumps
    old_dump1 = mock_kv_dir / "evicted_slot_0_old11111.bin"
    old_dump2 = mock_kv_dir / "evicted_slot_0_old22222.bin"
    old_dump1.write_bytes(b"old1")
    old_dump2.write_bytes(b"old2")

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.post", return_value=mock_resp) as mock_post, \
         patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/test_model.gguf")), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("os.path.getmtime", return_value=123456.0):

        saved = pm._save_kv_slot_for_eviction()
        assert saved is True

        assert mock_post.call_count == 1
        _, kwargs = mock_post.call_args
        saved_filename = kwargs["json"]["filename"]
        assert saved_filename.startswith("evicted_slot_0_")
        assert saved_filename not in ("evicted_slot_0_old11111.bin", "evicted_slot_0_old22222.bin")
        assert kwargs["timeout"] >= 15.0

        meta_file = mock_kv_dir / "slot_metadata.json"
        assert meta_file.exists()
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["filename"] == saved_filename

    # Now create the actual new file
    new_dump = mock_kv_dir / saved_filename
    new_dump.write_bytes(b"new_kv_data")

    # Post-stop cleanup keeps the active filename and deletes old dumps
    pm.clear_kv_slot_cache(keep_filename=saved_filename)

    assert new_dump.exists()
    assert not old_dump1.exists()
    assert not old_dump2.exists()


# -------------------------------------------------------------------------
# 2. Anomalous dump rejection and action=erase on restore timeout
# -------------------------------------------------------------------------
def test_anomalous_dump_size_rejected_and_cache_purged(mock_kv_dir):
    """Dumps exceeding 1.5x expected KV cache size or 40% available RAM must be rejected before restore."""
    pm = ProcessManager()

    huge_dump = mock_kv_dir / "evicted_slot_0_huge.bin"
    # Create a 3 GB sparse file instantly via truncate
    with open(huge_dump, "wb") as f:
        f.truncate(3 * 1024 * 1024 * 1024)  # 3 GB
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
         patch("requests.post") as mock_post:

        restored = pm._restore_kv_slot_after_start(meta)
        assert restored is False
        mock_post.assert_not_called()

    # Verify cache is cleared
    assert not meta_file.exists()


def test_restore_timeout_triggers_action_erase(mock_kv_dir):
    """When POST /slots/0?action=restore times out, action=erase must be safely dispatched."""
    pm = ProcessManager()
    pm._llama_process = MagicMock()
    pm._llama_process.poll.return_value = None
    pm._current_running_port = 1234

    dump_file = mock_kv_dir / "evicted_slot_0_test.bin"
    dump_file.write_bytes(b"data")
    meta_file = mock_kv_dir / "slot_metadata.json"
    meta = {
        "filename": "evicted_slot_0_test.bin",
        "model_path": "C:/models/test.gguf",
        "model_mtime": 100.0,
        "context_length": 8192,
        "gpu_layers": 48,
        "kv_cache_type_k": "q8_0",
        "kv_cache_type_v": "q8_0",
        "flash_attn": "on",
    }
    meta_file.write_text(json.dumps(meta), encoding="utf-8")

    def _post_router(url, *args, **kwargs):
        if "action=restore" in url:
            raise requests.exceptions.ReadTimeout("Restore timed out")
        elif "action=erase" in url:
            resp = MagicMock()
            resp.status_code = 200
            return resp
        return MagicMock(status_code=200)

    with patch("services.process_manager.ProcessManager.is_port_open", return_value=True), \
         patch("requests.post", side_effect=_post_router) as mock_post:

        restored = pm._restore_kv_slot_after_start(meta)
        assert restored is False

        # Verify action=erase was dispatched
        urls = [call[0][0] for call in mock_post.call_args_list]
        assert any("action=restore" in u for u in urls)
        assert any("action=erase" in u for u in urls)


# -------------------------------------------------------------------------
# 3. Host RAM OOM classification and GGML_CUDA_NO_PINNED retry
# -------------------------------------------------------------------------
def test_host_ram_oom_classification_and_no_ngl_drop(tmp_path, monkeypatch):
    """'unable to allocate CUDA_Host buffer' must set is_host_ram_oom_failure and retry with GGML_CUDA_NO_PINNED=1 without reducing NGL."""
    pm = ProcessManager()
    lms_client = LMStudioClient()
    lms_client.set_process_manager(pm)

    # Simulate log with CUDA_Host buffer error
    log_file = tmp_path / "llama_server.log"
    log_file.write_text(
        "ggml_backend_cuda_host_buffer_type: failed to allocate 512.0 MB\n"
        "error loading model: unable to allocate CUDA_Host buffer\n",
        encoding="utf-8"
    )
    monkeypatch.setattr("services.process_manager.LOGS_DIR", tmp_path)

    err_msg, is_oom = pm._extract_llama_startup_error()
    assert is_oom is True
    assert pm.is_host_ram_oom_failure is True
    assert pm.is_vram_oom_failure is False
    assert "Host RAM Out of Memory" in err_msg

    # Test that ensure_model_loaded retries without dropping NGL
    model_file = tmp_path / "test_model.gguf"
    model_file.write_text("gguf_bytes")
    monkeypatch.setattr("services.server_config.ServerConfigResolver.resolve_model_path", lambda m: model_file)
    monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: False)
    monkeypatch.setattr(lms_client, "check_health", lambda: (False, ""))

    start_calls = []

    def mock_start(model_identifier=None, context_length=None, gpu_layers=None, skip_kv_restore=False, **kwargs):
        start_calls.append({
            "gpu_layers": gpu_layers,
            "skip_kv_restore": skip_kv_restore,
            "force_no_pinned": getattr(pm, "_force_cuda_no_pinned", False),
        })
        if len(start_calls) == 1:
            return False  # First attempt fails with host OOM
        return True  # Retry with GGML_CUDA_NO_PINNED succeeds

    pm.start_llama_server_sync = mock_start

    res = lms_client.ensure_model_loaded(preferred_model="test_model", gpu_ratio="1.0")
    assert res is True
    assert len(start_calls) == 2

    # Second call must NOT drop NGL (it stays at 99 or original), but sets skip_kv_restore=True and force_no_pinned=True
    assert start_calls[1]["skip_kv_restore"] is True
    assert start_calls[1]["gpu_layers"] == start_calls[0]["gpu_layers"]
    assert start_calls[1]["force_no_pinned"] is True


# -------------------------------------------------------------------------
# 4. Crash diagnostics on ConnectionResetError
# -------------------------------------------------------------------------
def test_connection_error_diagnostics_on_crashed_headless_server(tmp_path, monkeypatch):
    """When headless llama-server crashes (rc != 0), chat_completion must report exact process crash details instead of external LM Studio message."""
    pm = ProcessManager()
    lms_client = LMStudioClient()
    lms_client.set_process_manager(pm)

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 3221225477  # 0xC0000005 Access Violation
    pm._llama_process = mock_proc
    pm.last_error = "Access violation in tensor compute"

    monkeypatch.setattr(pm, "has_headless_server", lambda: True)
    monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)

    def _conn_error(*a, **k):
        raise requests.exceptions.ConnectionError("Connection reset by peer (WinError 10054)")

    monkeypatch.setattr(lms_client._persistent_session, "post", _conn_error)

    with pytest.raises(ConnectionError) as exc_info:
        lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    err_str = str(exc_info.value)
    assert "Локальный процесс llama-server аварийно завершился (код 3221225477)" in err_str
    assert "Не удалось подключиться к LM Studio на" not in err_str
