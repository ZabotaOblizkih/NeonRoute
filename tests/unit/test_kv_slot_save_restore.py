"""
Unit tests for Step 4: Saving and restoring KV slot of llama-server
(--slot-save-path, POST /slots/0?action=save and action=restore)
across SD Forge eviction lifecycle.
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.process_manager import ProcessManager, ServerState


@pytest.fixture
def mock_kv_dir(tmp_path, monkeypatch):
    """Isolates KV_SLOTS_DIR into a temporary directory."""
    kv_dir = tmp_path / "kv_slots"
    kv_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("services.process_manager.KV_SLOTS_DIR", kv_dir)
    return kv_dir


def test_clear_kv_slot_cache_removes_dump_and_metadata(mock_kv_dir):
    """Verifies that clear_kv_slot_cache safely removes existing slot files."""
    pm = ProcessManager()

    slot_file = mock_kv_dir / "evicted_slot_0.bin"
    meta_file = mock_kv_dir / "slot_metadata.json"
    slot_file.write_bytes(b"dummy_slot_data")
    meta_file.write_text('{"dummy": true}', encoding="utf-8")

    assert slot_file.exists()
    assert meta_file.exists()

    pm.clear_kv_slot_cache()

    assert not slot_file.exists()
    assert not meta_file.exists()


def test_successful_kv_slot_save_and_restore_cycle(mock_kv_dir):
    """
    Scenario 1:
    - Save slot 0 before Forge eviction (creates slot_metadata.json).
    - Restore slot 0 on reload with matching metadata.
    - Verifies slot files are purged immediately after restore (single-use dump).
    """
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

    fake_mtime = 1700000000.0

    # 1. Save slot
    mock_save_resp = MagicMock()
    mock_save_resp.status_code = 200
    mock_save_resp.json.return_value = {"id_slot": 0, "filename": "evicted_slot_0.bin"}

    with patch("requests.post", return_value=mock_save_resp) as mock_post, \
         patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/gemma-4-12b.gguf")), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("os.path.getmtime", return_value=fake_mtime):

        saved = pm._save_kv_slot_for_eviction()
        assert saved is True

        # Check request payload
        assert mock_post.call_count == 1
        call_args, call_kwargs = mock_post.call_args
        assert call_args[0] == "http://127.0.0.1:1234/slots/0?action=save"
        saved_filename = call_kwargs["json"]["filename"]
        assert saved_filename.startswith("evicted_slot_0_")
        assert call_kwargs["timeout"] >= 15.0

        # Check metadata file
        meta_file = mock_kv_dir / "slot_metadata.json"
        assert meta_file.exists()
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert meta["filename"] == saved_filename
        assert meta["context_length"] == 14336
        assert meta["gpu_layers"] == 48
        assert meta["kv_cache_type_k"] == "q8_0"
        assert meta["kv_cache_type_v"] == "q4_0"
        assert meta["flash_attn"] == "on"
        assert meta["model_mtime"] == fake_mtime

    # Simulate llama-server writing the slot file
    slot_file = mock_kv_dir / saved_filename
    slot_file.write_bytes(b"llama_slot_kv_binary_content")

    # 2. Restore slot with matching parameters
    expected_meta = {
        "model_path": "C:/models/gemma-4-12b.gguf",
        "model_mtime": fake_mtime,
        "context_length": 14336,
        "gpu_layers": 48,
        "kv_cache_type_k": "q8_0",
        "kv_cache_type_v": "q4_0",
        "flash_attn": "on",
    }

    mock_restore_resp = MagicMock()
    mock_restore_resp.status_code = 200
    mock_restore_resp.json.return_value = {"id_slot": 0, "filename": saved_filename}

    with patch("requests.post", return_value=mock_restore_resp) as mock_post_restore:
        restored = pm._restore_kv_slot_after_start(expected_meta)
        assert restored is True

        assert mock_post_restore.call_count == 1
        restore_args, restore_kwargs = mock_post_restore.call_args
        assert restore_args[0] == "http://127.0.0.1:1234/slots/0?action=restore"
        assert restore_kwargs["json"]["filename"] == saved_filename
        assert restore_kwargs["timeout"] >= 15.0

    # 3. Verify single-use hygiene: metadata is cleared immediately, while dump file is cleaned up on post-stop
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
    """
    Scenario 2:
    - If any key (model_path, mtime, context, gpu_layers, ctv, flash_attn) changed between
      eviction and startup, restore must be skipped and files purged.
    """
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
        restored = pm._restore_kv_slot_after_start(expected_meta)
        assert restored is False
        # action=restore must NOT be sent
        mock_post.assert_not_called()

    # Invalidation cleans up metadata immediately; dump file is purged on post-stop
    assert not meta_file.exists()
    pm.clear_kv_slot_cache()
    assert not slot_file.exists()


def test_kv_slot_save_and_restore_fault_tolerance(mock_kv_dir):
    """
    Scenario 3:
    - Network timeout or HTTP error on save/restore does NOT crash caller.
    - evict_llm_for_forge_sync proceeds normally.
    - Residual/corrupt files are cleaned up.
    """
    pm = ProcessManager()
    pm._llama_process = MagicMock()
    pm._current_running_port = 1234
    pm._current_running_model_path = "C:/models/test.gguf"

    # 1. Save timeout
    with patch("requests.post", side_effect=requests.Timeout("Connection timeout")):
        saved = pm._save_kv_slot_for_eviction()
        assert saved is False
        assert not (mock_kv_dir / "slot_metadata.json").exists()

    # 2. evict_llm_for_forge_sync completes gracefully even if save fails
    with patch.object(pm, "_save_kv_slot_for_eviction", return_value=False), \
         patch.object(pm, "_kill_llama_server_instance") as mock_kill:
        evicted = pm.evict_llm_for_forge_sync()
        assert evicted is True
        mock_kill.assert_called_once_with(for_forge_eviction=True)

    # 3. Restore HTTP 500 error
    slot_file = mock_kv_dir / "evicted_slot_0.bin"
    meta_file = mock_kv_dir / "slot_metadata.json"
    slot_file.write_bytes(b"data")
    meta = {
        "model_path": "C:/models/test.gguf",
        "model_mtime": 100.0,
        "context_length": 8192,
        "kv_cache_type_k": "q8_0",
        "kv_cache_type_v": "q8_0",
        "gpu_layers": 99,
        "flash_attn": "on",
    }
    meta_file.write_text(json.dumps(meta), encoding="utf-8")

    mock_err_resp = MagicMock()
    mock_err_resp.status_code = 500
    mock_err_resp.text = "Internal Server Error in slot restore"

    with patch("requests.post", return_value=mock_err_resp):
        restored = pm._restore_kv_slot_after_start(meta)
        assert restored is False

    # Metadata invalidated; dump file cleaned up in post-stop cleanup
    assert not meta_file.exists()
    pm.clear_kv_slot_cache()
    assert not slot_file.exists()


def test_clear_kv_slot_cache_invoked_on_session_switch_and_shutdown(qapp):
    """
    Scenario 4:
    - SessionController._switch_to_session() and _create_new_session() call clear_kv_slot_cache().
    - ShutdownWorker.run() calls clear_kv_slot_cache() before shutdown_all().
    """
    # 1. SessionController
    from PyQt6.QtWidgets import QWidget
    from ui.controllers.session_controller import SessionControllerMixin
    from services.sandbox_manager import SandboxManager

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
        # Test _create_new_session invokes clear_kv_slot_cache right at the start
        with patch("services.session_manager.SessionManager.create_session", side_effect=RuntimeError("stop_after_cache_clear")):
            try:
                win._create_new_session()
            except RuntimeError:
                pass
        win.process_manager.clear_kv_slot_cache.assert_called_once()

        win.process_manager.clear_kv_slot_cache.reset_mock()

        # Test _switch_to_session invokes clear_kv_slot_cache
        with patch("services.session_manager.SessionManager.load_session", return_value=None):
            win._switch_to_session("test-session-id")
        win.process_manager.clear_kv_slot_cache.assert_called_once()
    finally:
        SandboxManager.set_active_session_id(None)
        win.deleteLater()

    # 2. ShutdownWorker
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
