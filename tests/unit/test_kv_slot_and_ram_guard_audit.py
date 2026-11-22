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
         patch.object(pm, "erase_kv_slot_sync") as mock_erase, \
         patch("requests.post") as mock_post:

        restored = pm._restore_kv_slot_after_start(meta)
        assert restored is False
        mock_post.assert_not_called()
        mock_erase.assert_called_once_with(slot_id=0)

    # Metadata file is invalidated immediately
    assert not meta_file.exists()
    # Dump file remains open-safe until post-stop cleanup (descriptors closed)
    assert huge_dump.exists()
    pm.clear_kv_slot_cache()
    assert not huge_dump.exists()


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


# -------------------------------------------------------------------------
# 5. Review Fix: LayerBudgeter host RAM calculation and Host OOM retry exhaustion
# -------------------------------------------------------------------------
def test_layer_budgeter_ram_not_clamped_by_small_pagefile(monkeypatch):
    """When physical RAM is plentiful (>= 1500 MB), get_available_host_memory_mb must NOT clamp to small pagefile."""
    from services.layer_budgeter import LayerBudgeter
    import sys
    import ctypes

    class FakeMemoryStatus:
        def __init__(self, phys_mb, pagefile_mb):
            self.ullAvailPhys = int(phys_mb * 1024 * 1024)
            self.ullAvailPageFile = int(pagefile_mb * 1024 * 1024)
            self.dwLength = 64

    fake_stat = FakeMemoryStatus(phys_mb=6000.0, pagefile_mb=800.0)

    def mock_global_mem_status(byref_stat):
        byref_stat._obj.ullAvailPhys = fake_stat.ullAvailPhys
        byref_stat._obj.ullAvailPageFile = fake_stat.ullAvailPageFile
        return 1

    monkeypatch.setattr(sys, "platform", "win32")
    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes.windll.kernel32, "GlobalMemoryStatusEx", mock_global_mem_status)

    avail_mb = LayerBudgeter.get_available_host_memory_mb()
    assert avail_mb >= 6000.0, f"Expected >= 6000 MB, got {avail_mb}"


def test_host_ram_oom_retry_failure_terminates_with_clear_error(tmp_path, monkeypatch):
    """When host RAM OOM retry also fails, it must terminate with LlamaServerStartupError and NOT retry further."""
    from services.lm_studio import LlamaServerStartupError
    pm = ProcessManager()
    lms_client = LMStudioClient()
    lms_client.set_process_manager(pm)

    pm.is_host_ram_oom_failure = True
    pm.is_vram_oom_failure = False
    pm.last_error = "Host RAM Out of Memory: unable to allocate CUDA_Host buffer"

    model_file = tmp_path / "test_model.gguf"
    model_file.write_text("gguf_bytes")
    monkeypatch.setattr("services.server_config.ServerConfigResolver.resolve_model_path", lambda m: model_file)
    monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: False)
    monkeypatch.setattr(lms_client, "check_health", lambda: (False, ""))

    retry_count = 0

    def mock_start(**kwargs):
        nonlocal retry_count
        retry_count += 1
        return False  # Both initial and retry fail

    pm.start_llama_server_sync = mock_start

    with pytest.raises(LlamaServerStartupError) as exc_info:
        lms_client.ensure_model_loaded(preferred_model="test_model", gpu_ratio="1.0")

    # Exactly 2 calls (1 initial + 1 retry with GGML_CUDA_NO_PINNED=1)
    assert retry_count == 2
    err_str = str(exc_info.value)
    assert "Недостаточно системной памяти хоста" in err_str
    assert "GGML_CUDA_NO_PINNED=1" in err_str


# -------------------------------------------------------------------------
# 6. Bad allocation Host OOM classification & Forge sd_checkpoints_keep_in_cpu
# -------------------------------------------------------------------------
def test_bad_allocation_classified_as_host_ram_oom(tmp_path, monkeypatch):
    """'error loading model: bad allocation' must be classified as Host RAM OOM, never VRAM OOM."""
    pm = ProcessManager()
    log_file = tmp_path / "llama_server.log"
    log_file.write_text("error loading model: bad allocation\n", encoding="utf-8")
    monkeypatch.setattr("services.process_manager.LOGS_DIR", tmp_path)

    err_msg, is_oom = pm._extract_llama_startup_error()
    assert is_oom is True
    assert pm.is_host_ram_oom_failure is True
    assert pm.is_vram_oom_failure is False
    assert "Host RAM Out of Memory" in err_msg
    assert "CUDA Out of Memory" not in err_msg


def test_forge_client_options_disable_checkpoints_keep_in_cpu(monkeypatch):
    """ForgeClient must pass sd_checkpoints_keep_in_cpu=False on systems <= 16.5 GB RAM."""
    from services.forge_client import ForgeClient
    fc = ForgeClient()

    sent_options = []

    def mock_post(url, json=None, timeout=None):
        if "/options" in url:
            sent_options.append(json)
            resp = MagicMock()
            resp.status_code = 200
            return resp
        elif "/sd-models" in url:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = [{"title": "test_ckpt.safetensors", "model_name": "test_ckpt", "filename": "test_ckpt.safetensors"}]
            return resp
        return MagicMock(status_code=200)

    monkeypatch.setattr("requests.post", mock_post)
    monkeypatch.setattr("requests.get", mock_post)
    monkeypatch.setattr(fc, "is_server_ready", lambda timeout=1.0: True)
    monkeypatch.setattr(fc, "get_current_model", lambda: "other_model.safetensors")

    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(total=16.0 * (1024 ** 3), available=4.0 * (1024 ** 3)))

    # Test ensure_system_options
    fc._system_options_ensured = False
    ok = fc.ensure_system_options(force=True)
    assert ok is True
    assert len(sent_options) == 1
    assert sent_options[0].get("sd_checkpoints_keep_in_cpu") is False
    assert sent_options[0].get("sd_checkpoint_cache") == 0
    assert sent_options[0].get("sd_checkpoints_limit") == 1

    # Test set_model
    sent_options.clear()
    ok_set = fc.set_model("test_ckpt.safetensors")
    assert ok_set is True
    assert len(sent_options) == 1
    assert sent_options[0].get("sd_checkpoints_keep_in_cpu") is False
    assert sent_options[0].get("sd_checkpoint_cache") == 0
    assert sent_options[0].get("sd_checkpoints_limit") == 1


# -------------------------------------------------------------------------
# 7. End-to-End Smoke Tests: Forge Unload & Llama Host RAM Guard Cycle
# -------------------------------------------------------------------------
def test_e2e_smoke_forge_unload_and_ram_guard_cycle(monkeypatch):
    """
    E2E Smoke: Forge unload and RAM guard cycle with VRAMArbiter.

    Verifies:
    1. Checkpoint unload scenario with CPU RAM/cache control flags (<= 16.5 GB vs > 16.5 GB).
    2. Dynamic wait for VRAM clear (_wait_for_vram_clear) with polling / retry check.
    3. Resource ownership transition (GPUOwner.DIFFUSION -> GPUOwner.LLM).
    4. Safe release of locks (_unload_lock).
    """
    import psutil
    from services.forge_client import ForgeClient
    from services.vram_arbiter import GPUOwner, VRAMArbiter

    monkeypatch.setattr("services.forge_client.time.sleep", lambda s: None)
    monkeypatch.setattr("services.vram_arbiter.time.sleep", lambda s: None)

    # --- Сценарий 1: Система <= 16.5 GB RAM (8GB VRAM / 16GB RAM) ---
    monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(total=16.0 * (1024 ** 3), available=4.0 * (1024 ** 3)))

    fc = ForgeClient()
    arbiter = VRAMArbiter(forge_client=fc)
    arbiter._current_owner = GPUOwner.DIFFUSION

    posted_urls = []
    sent_options = []

    def mock_post(url, json=None, timeout=None):
        posted_urls.append(url)
        if "/options" in url and json:
            sent_options.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "ok"}
        return resp

    def mock_get(url, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if "/options" in url:
            resp.json.return_value = {"sd_model_checkpoint": "test_checkpoint.safetensors"}
        elif "/sd-models" in url:
            resp.json.return_value = [{"title": "test_checkpoint.safetensors", "model_name": "test_checkpoint", "filename": "test_checkpoint.safetensors"}]
        return resp

    monkeypatch.setattr("requests.post", mock_post)
    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr(fc, "is_server_ready", lambda timeout=1.0: True)

    # Имитируем кратковременное ожидание освобождения VRAM:
    # 1-й замер: VRAM еще занята (1500 МБ < 3800 МБ), 2-й: VRAM освобождена (4500 МБ >= 3800 МБ)
    vram_measurements = [1500, 4500, 4500, 4500]
    mock_monitor = MagicMock()
    mock_monitor.get_total_vram_mb.return_value = 8192
    mock_monitor.get_free_vram_mb.side_effect = lambda: vram_measurements.pop(0) if vram_measurements else 4500
    fc.system_monitor = mock_monitor

    # Замокаем LayerBudgeter.get_current_used_vram_mb для стабилизации
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.get_current_used_vram_mb", lambda: 300.0)
    monkeypatch.setattr(arbiter, "_wait_for_vram_stabilization", lambda: None)

    # 1.1. Проверка установки опций кэша CPU для <= 16.5 GB RAM
    ok_opts = fc.ensure_system_options(force=True)
    assert ok_opts is True
    assert len(sent_options) == 1
    assert sent_options[0].get("sd_checkpoints_keep_in_cpu") is False
    assert sent_options[0].get("sd_checkpoint_cache") == 0
    assert sent_options[0].get("sd_checkpoints_limit") == 1

    # 1.2. Проверка цикла _wait_for_vram_clear() напрямую с кратковременным ожиданием
    fc._last_vram_wait_ts = 0.0
    fc._wait_for_vram_clear()
    assert fc.system_monitor.get_free_vram_mb.called

    # 1.3. Выполнение переключения владельца под LLM через арбитр
    assert arbiter._current_owner == GPUOwner.DIFFUSION
    arbiter.prepare_for_task("llm")

    # Проверка вызова эндпоинтов разгрузки Forge
    assert any("/unload-checkpoint" in u for u in posted_urls)
    assert any("/free-vram" in u for u in posted_urls)

    # Проверка смены состояния арбитра и корректного освобождения блокировки
    assert arbiter._current_owner == GPUOwner.LLM
    assert fc._checkpoint_unloaded is True
    assert not fc._unload_lock.locked()

    # --- Сценарий 2: Система > 16.5 GB RAM (32GB RAM) ---
    monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(total=32.0 * (1024 ** 3), available=20.0 * (1024 ** 3)))
    fc32 = ForgeClient()
    sent_options_32 = []

    def mock_post_32(url, json=None, timeout=None):
        if "/options" in url and json:
            sent_options_32.append(json)
        return MagicMock(status_code=200)

    monkeypatch.setattr("requests.post", mock_post_32)
    ok_opts_32 = fc32.ensure_system_options(force=True)
    assert ok_opts_32 is True
    # На 32 GB RAM опции ограничения памяти не отправляются
    assert len(sent_options_32) == 0


def test_e2e_smoke_llama_server_bad_allocation_and_host_ram_protection(tmp_path, monkeypatch):
    """
    E2E Smoke: llama-server startup with bad allocation, host RAM OOM handling, and KV slot protection.

    Verifies:
    1. Error 'error loading model: bad allocation' classified as Host RAM OOM (is_host_ram_oom_failure=True, is_vram_oom_failure=False).
    2. NGL is preserved (not decremented 46 -> 42).
    3. Host RAM guard activates (GGML_CUDA_NO_PINNED=1, skip_kv_restore=True).
    4. KV slot dump and metadata in tmp_path are cleaned up properly without leftover trash.
    """
    kv_dir = tmp_path / "kv_slots"
    kv_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("services.process_manager.KV_SLOTS_DIR", kv_dir)
    monkeypatch.setattr("services.process_manager.LOGS_DIR", logs_dir)

    # Создаем фиктивный снимок KV-слота и файл метаданных в tmp_path
    slot_file = kv_dir / "evicted_slot_0_deadbeef.bin"
    slot_file.write_bytes(b"dummy_evicted_kv_data")
    meta_file = kv_dir / "slot_metadata.json"
    meta_file.write_text(json.dumps({
        "filename": slot_file.name,
        "model_path": "C:/models/test_model.gguf",
        "gpu_layers": 46,
        "context_length": 8192
    }), encoding="utf-8")

    pm = ProcessManager()
    lms_client = LMStudioClient()
    lms_client.set_process_manager(pm)

    # Изолируем окружение и зависимости
    model_file = tmp_path / "test_model.gguf"
    model_file.write_text("gguf_test_bytes")

    monkeypatch.setattr("services.server_config.ServerConfigResolver.resolve_model_path", lambda m: model_file)
    monkeypatch.setattr("services.server_config.ServerConfigResolver.resolve_server_exe", lambda: Path("C:/llama/llama-server.exe"))
    monkeypatch.setattr("services.server_config.ServerConfigResolver.load_ini", lambda: {"host": "127.0.0.1", "port": 1234, "gpu_layers": 46})
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.calculate_layer_offload", lambda **k: (46, True, "", {"total_layers": 48}))
    monkeypatch.setattr(pm, "inspect_port_occupant", lambda port: (False, None, None))
    monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: False)
    monkeypatch.setattr(lms_client, "check_health", lambda: (False, ""))

    llama_log = logs_dir / "llama_server.log"

    attempts = []
    recorded_first_host_oom = None
    recorded_first_vram_oom = None

    def mock_popen(cmd, env=None, **kwargs):
        attempt_no = len(attempts) + 1
        cuda_no_pinned = env.get("GGML_CUDA_NO_PINNED") if env else None
        attempts.append({
            "attempt": attempt_no,
            "cmd": list(cmd),
            "cuda_no_pinned": cuda_no_pinned,
        })

        mock_p = MagicMock()
        mock_p.stdout = None
        mock_p.stderr = None

        if attempt_no == 1:
            # Имитируем падение с bad allocation при первой попытке
            mock_p.poll.return_value = 1
            llama_log.write_text("error loading model: bad allocation\nllama_init_from_file: failed to load model\n", encoding="utf-8")
        else:
            # Второй запуск (с защитой хоста) успешен
            mock_p.poll.return_value = None
            llama_log.write_text("llama_server: ready to accept requests\n", encoding="utf-8")
        return mock_p

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    monkeypatch.setattr("services.process_manager.Win32JobManager.get_instance", lambda: MagicMock(assign_process=lambda p: True))

    def mock_wait_ready(host="127.0.0.1", port=1234, timeout_s=60.0):
        nonlocal recorded_first_host_oom, recorded_first_vram_oom
        if len(attempts) == 1:
            # Первый запуск упал
            detail_msg, is_oom = pm._extract_llama_startup_error()
            pm.is_oom_failure = is_oom
            recorded_first_host_oom = pm.is_host_ram_oom_failure
            recorded_first_vram_oom = pm.is_vram_oom_failure
            pm._set_server_state(ServerState.ERROR, f"Process crashed: {detail_msg}")
            return False
        # Второй запуск готов
        pm._set_server_state(ServerState.READY, "Model loaded")
        return True

    monkeypatch.setattr(pm, "_wait_for_server_ready", mock_wait_ready)

    try:
        # Запускаем сквозной сценарий через lms_client.ensure_model_loaded
        res = lms_client.ensure_model_loaded(preferred_model="test_model", context_length=8192)
        assert res is True

        # Проверки попыток запуска
        assert len(attempts) == 2

        # 1. Первая попытка: обычный запуск без принудительного NO_PINNED
        assert attempts[0]["cuda_no_pinned"] is None or attempts[0]["cuda_no_pinned"] != "1"

        # 2. Ошибка bad allocation была классифицирована как Host RAM OOM (не VRAM OOM)
        assert recorded_first_host_oom is True
        assert recorded_first_vram_oom is False

        # 3. Вторая попытка: NGL НЕ был снижен до 42, остался 46!
        cmd1 = attempts[0]["cmd"]
        cmd2 = attempts[1]["cmd"]
        ngl1 = cmd1[cmd1.index("-ngl") + 1] if "-ngl" in cmd1 else None
        ngl2 = cmd2[cmd2.index("-ngl") + 1] if "-ngl" in cmd2 else None
        assert ngl1 == "46"
        assert ngl2 == "46"
        assert attempts[1]["cuda_no_pinned"] == "1"
        assert pm._server_state == ServerState.READY

        # 4. Проверка очистки KV слотов при skip_kv_restore=True
        assert not slot_file.exists()
        assert not meta_file.exists()

    finally:
        # Гарантированная очистка слотов
        pm.clear_kv_slot_cache()
        assert not list(kv_dir.glob("*.bin"))
        assert not list(kv_dir.glob("*.json"))

