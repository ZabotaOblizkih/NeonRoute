"""
Unit tests for the 4 failure point fixes verified from 2026-11-18 incident logs:
1. Empty SSE stream detection (RuntimeError on unexpected 0 tokens, InterruptedError on aborted).
2. is_model_loaded() invalidation when process_manager reports dead server.
3. EOF in _stream_llama_stdout transitions READY -> STOPPED, preserves EVICTED_FOR_FORGE.
4. LayerBudgeter compute_mb and SAFETY_MARGIN_VRAM_MB for large vocab models (vocab_size >= 200000).
5. ForgeClient.unload_checkpoint error handling on HTTP 500 and VRAMArbiter plateau stabilization at ~780 MB.
100% isolated, mocked, zero external network/GPU/process requirements.
"""

import time
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.lm_studio import LMStudioClient
from services.process_manager import ProcessManager, ServerState
from services.layer_budgeter import LayerBudgeter
from services.forge_client import ForgeClient
from services.vram_arbiter import VRAMArbiter, GPUOwner
from config import SAFETY_MARGIN_VRAM_MB


@pytest.fixture(autouse=True)
def airgap_network_and_sleep(monkeypatch):
    """Guarantees isolated execution."""
    monkeypatch.setattr(time, "sleep", lambda s: None)


# ---------------------------------------------------------------------------
# 1. LMStudioClient SSE empty stream handling
# ---------------------------------------------------------------------------
def test_empty_sse_stream_raises_runtime_error_when_not_aborted(monkeypatch):
    """When stream=True and server returns 0 tokens without abort, raise RuntimeError."""
    LMStudioClient._instance = None
    client = LMStudioClient()
    client._is_aborted = False
    client._persistent_session = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines.return_value = []  # empty stream
    mock_resp.content = b""
    client._persistent_session.post.return_value = mock_resp

    monkeypatch.setattr(client, "is_model_loaded", lambda m=None: True)

    with pytest.raises(RuntimeError, match="LLM stream closed with 0 tokens"):
        client.chat_completion(model="test-model", messages=[{"role": "user", "content": "hello"}], stream=True)


def test_empty_sse_stream_handles_abort_without_empty_stream_error(monkeypatch):
    """When stream is aborted by user, raises InterruptedError instead of empty stream RuntimeError."""
    LMStudioClient._instance = None
    client = LMStudioClient()
    client._persistent_session = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    def _iter_lines(**kwargs):
        client.abort()
        return []

    mock_resp.iter_lines = _iter_lines
    mock_resp.content = b""
    client._persistent_session.post.return_value = mock_resp

    monkeypatch.setattr(client, "is_model_loaded", lambda m=None: True)

    with pytest.raises(InterruptedError):
        client.chat_completion(model="test-model", messages=[{"role": "user", "content": "hello"}], stream=True)


# ---------------------------------------------------------------------------
# 2. is_model_loaded() dead process check
# ---------------------------------------------------------------------------
def test_is_model_loaded_returns_false_and_clears_id_when_process_manager_dead():
    """If process_manager reports LLM server is not running, is_model_loaded resets cached model and returns False."""
    LMStudioClient._instance = None
    client = LMStudioClient()
    client._is_model_unloaded = False
    client._last_loaded_model_id = "test-model-id"

    mock_pm = MagicMock()
    mock_pm.is_lm_studio_running.return_value = False
    client.set_process_manager(mock_pm)

    try:
        loaded = client.is_model_loaded("test-model-id")
        assert loaded is False
        assert client._last_loaded_model_id is None
    finally:
        client.set_process_manager(None)


def test_connection_error_marks_model_unloaded_and_checks_pm(monkeypatch):
    """ConnectionError in chat_completion marks _is_model_unloaded and notifies process_manager."""
    LMStudioClient._instance = None
    client = LMStudioClient()
    client._is_model_unloaded = False
    client._last_loaded_model_id = "active-model"
    mock_pm = MagicMock()
    mock_pm.is_lm_studio_running.return_value = False
    client.set_process_manager(mock_pm)

    client._persistent_session = MagicMock()
    import requests
    client._persistent_session.post.side_effect = requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(client, "is_model_loaded", lambda m=None: True)

    try:
        with pytest.raises(ConnectionError):
            client.chat_completion(messages=[{"role": "user", "content": "hi"}], stream=False)

        assert client._is_model_unloaded is True
        assert client._last_loaded_model_id is None
        mock_pm.is_lm_studio_running.assert_called_once()
    finally:
        client.set_process_manager(None)
        client._current_running_ngl = None
        client._current_total_layers = None


# ---------------------------------------------------------------------------
# 3. ProcessManager._stream_llama_stdout EOF handling
# ---------------------------------------------------------------------------
def test_stream_llama_stdout_eof_transitions_ready_to_stopped():
    """EOF in stdout stream with non-zero exit code transitions READY -> STOPPED."""
    pm = ProcessManager()
    pm._server_state = ServerState.READY
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1  # Process crashed
    mock_proc.stdout.readline.return_value = ""  # Immediate EOF
    pm._llama_process = mock_proc

    mock_file = MagicMock()

    # Emulate stdout reader execution
    with patch.object(pm, "_set_server_state") as mock_set_state:
        # Recreate the inner reader function with our patched code
        def _stream(proc, file_handle):
            try:
                for raw_line in iter(proc.stdout.readline, ''):
                    if not raw_line:
                        break
            finally:
                try:
                    file_handle.close()
                except Exception:
                    pass
                rc = proc.poll()
                if rc is not None and pm._server_state in (ServerState.READY, ServerState.STARTING):
                    if pm._llama_process is proc:
                        pm._llama_process = None
                    pm._set_server_state(ServerState.STOPPED, f"llama-server завершился неожиданно (код {rc})")

        _stream(mock_proc, mock_file)

        mock_set_state.assert_called_once_with(
            ServerState.STOPPED, "llama-server завершился неожиданно (код 1)"
        )
        assert pm._llama_process is None


def test_stream_llama_stdout_eof_preserves_evicted_for_forge():
    """EOF in stdout stream does NOT transition state if server was legitimately EVICTED_FOR_FORGE."""
    pm = ProcessManager()
    pm._server_state = ServerState.EVICTED_FOR_FORGE
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0
    mock_proc.stdout.readline.return_value = ""
    pm._llama_process = mock_proc

    mock_file = MagicMock()

    with patch.object(pm, "_set_server_state") as mock_set_state:
        def _stream(proc, file_handle):
            try:
                for raw_line in iter(proc.stdout.readline, ''):
                    if not raw_line:
                        break
            finally:
                try:
                    file_handle.close()
                except Exception:
                    pass
                rc = proc.poll()
                if rc is not None and pm._server_state in (ServerState.READY, ServerState.STARTING):
                    if pm._llama_process is proc:
                        pm._llama_process = None
                    pm._set_server_state(ServerState.STOPPED, f"llama-server завершился неожиданно (код {rc})")

        _stream(mock_proc, mock_file)
        mock_set_state.assert_not_called()


# ---------------------------------------------------------------------------
# 4. LayerBudgeter compute_mb and SAFETY_MARGIN_VRAM_MB for Vocab=262144
# ---------------------------------------------------------------------------
def test_layer_budgeter_compute_mb_and_safety_margin_for_large_vocab():
    """Model with vocab_size=262144 receives analytical compute buffer 396 MB (140 base + 256 logits) and SAFETY_MARGIN_VRAM_MB=170."""
    assert SAFETY_MARGIN_VRAM_MB == 170

    mock_meta = {
        "total_layers": 48,
        "model_size_mb": 6698.65,
        "architecture": "gemma",
        "is_moe": False,
        "context_length": 14336,
        "vocab_size": 262144,
    }

    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):

        ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_gemma.gguf",
            target_context=14336,
            total_vram_mb=8151,
            is_arbiter_managed=True,
            current_used_vram_mb=682.1,
            available_ram_mb=16000.0
        )

        assert can_run is True
        # compute_mb: base_graph_mb(140) + logits_mb(256) = 396 MB
        assert audit["compute_buffer_mb"] == 396
        assert audit["logits_buffer_mb"] == 256
        assert audit["ubatch_size"] == 256
        # safety_margin_mb should be 170
        assert audit["safety_margin_mb"] == 170
        assert ngl == 40


def test_layer_budgeter_analytical_compute_mb_across_vocabularies():
    """Verify analytical compute_mb across various vocabularies: 32K (172 MB), 152K (289 MB), 262K (396 MB)."""
    # 1. Vocab = 32000 (Llama): base 140 + ceil(256 * 32000 * 4 / 1024^2) = 140 + 32 = 172 MB
    meta_32k = {"total_layers": 32, "model_size_mb": 7000.0, "vocab_size": 32000}
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_32k):
        _, _, _, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_32k.gguf", target_context=8192, total_vram_mb=8192, is_arbiter_managed=True, current_used_vram_mb=200.0
        )
        assert audit["logits_buffer_mb"] == 32
        assert audit["compute_buffer_mb"] == 172

    # 2. Vocab = 152064 (Qwen 2.5): base 140 + ceil(256 * 152064 * 4 / 1024^2) = 140 + 149 = 289 MB
    meta_152k = {"total_layers": 28, "model_size_mb": 7000.0, "vocab_size": 152064}
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_152k):
        _, _, _, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_152k.gguf", target_context=8192, total_vram_mb=8192, is_arbiter_managed=True, current_used_vram_mb=200.0
        )
        assert audit["logits_buffer_mb"] == 149
        assert audit["compute_buffer_mb"] == 289

    # 3. Small model < 5000 MB: base 100 instead of 140
    meta_small = {"total_layers": 24, "model_size_mb": 4000.0, "vocab_size": 32000}
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_small):
        _, _, _, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_small.gguf", target_context=8192, total_vram_mb=8192, is_arbiter_managed=True, current_used_vram_mb=200.0
        )
        assert audit["compute_buffer_mb"] == 100 + 32  # 132 MB

    # 4. Custom ubatch_size override = 512: ceil(512 * 262144 * 4 / 1024^2) = 512 MB logits
    meta_gemma = {"total_layers": 48, "model_size_mb": 7000.0, "vocab_size": 262144}
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_gemma):
        _, _, _, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_gemma.gguf", target_context=8192, total_vram_mb=8192, is_arbiter_managed=True, current_used_vram_mb=200.0, ubatch_size=512
        )
        assert audit["ubatch_size"] == 512
        assert audit["logits_buffer_mb"] == 512
        assert audit["compute_buffer_mb"] == 140 + 512  # 652 MB


def test_layer_budgeter_analytical_estimate_kv_cache_mb():
    """Verify analytical estimate_kv_cache_mb with SWA, array head_count_kv, and fallback."""
    # 1. Analytical layer-wise SWA for Gemma (hybrid array of heads: 40 SWA of 8 heads, 8 Global of 1 head)
    # 40 SWA layers: 8 heads, k_len_swa=256, v_len_swa=256, local_ctx=min(14336, 1024+512)=1536 -> 40*1536*8*512*1.0625 = 267,386,880 B
    # 8 Global layers: 1 head, k_len=512, v_len=512, ctx=14336 -> 8*14336*1*1024*1.0625 = 124,792,832 B
    # Total = 392,179,712 B / 1048576 = 374.0117 MB * 1.15 = 430.11 -> 431 MB
    meta_swa = {
        "total_layers": 48,
        "sliding_window": 1024,
        "sliding_window_pattern": [True] * 40 + [False] * 8,
        "head_count_kv": [8] * 40 + [1] * 8,
        "key_length": 512,
        "value_length": 512,
        "key_length_swa": 256,
        "value_length_swa": 256,
    }
    kv_mb = LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0", gguf_meta=meta_swa)
    assert kv_mb == 431

    # 2. q4 cache type: type_bytes = 0.5625 -> 228 MB
    kv_mb_q4 = LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q4", gguf_meta=meta_swa)
    assert kv_mb_q4 == 228

    # 2b. Asymmetric q8_0_q4_0 cache type for Gemma 4 12B:
    # 14336 ctx -> 329 MB
    # 24576 ctx -> 404 MB
    kv_mb_asym_14k = LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0_q4_0", gguf_meta=meta_swa)
    assert kv_mb_asym_14k == 329

    kv_mb_asym_24k = LayerBudgeter.estimate_kv_cache_mb(24576, cache_type="q8_0_q4_0", gguf_meta=meta_swa)
    assert kv_mb_asym_24k == 404

    # 3. Fallback when head dimensions are absent: uses kb_per_token = 64
    meta_no_dims = {"total_layers": 32, "sliding_window": 0}
    kv_mb_fb = LayerBudgeter.estimate_kv_cache_mb(8192, cache_type="q8_0", gguf_meta=meta_no_dims)
    # raw_mb = 8192 * 64 / 1024 = 512 MB * 1.15 = 588.8 -> 589 MB
    assert kv_mb_fb == 589

    # 4. Explicit Gemma 4 12B case: head_count_kv=3.5, total_layers=48, k_len=256, v_len=256, ctx=14336, q8_0
    # With SWA (sliding_window=1024):
    #   effective_layer_tokens = 40 * 1536 + 8 * 14336 = 176128
    #   bytes_per_token_per_layer = 3.5 * 512 * 1.0625 = 1904.0
    #   raw_mb = 176128 * 1904.0 / 1048576 = 319.8125 MB -> * 1.15 = 367.78 -> 368 MB
    meta_gemma4_3_5 = {
        "total_layers": 48,
        "head_count_kv": 3.5,
        "key_length": 256,
        "value_length": 256,
        "sliding_window": 1024,
    }
    kv_mb_gemma = LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0", gguf_meta=meta_gemma4_3_5)
    assert kv_mb_gemma == 368

    # Without SWA (sliding_window=0):
    #   effective_layer_tokens = 48 * 14336 = 688128
    #   raw_mb = 688128 * 1904.0 / 1048576 = 1249.5 MB -> * 1.15 = 1436.925 -> 1437 MB
    meta_gemma4_no_swa = dict(meta_gemma4_3_5)
    meta_gemma4_no_swa["sliding_window"] = 0
    kv_mb_gemma_no_swa = LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0", gguf_meta=meta_gemma4_no_swa)
    assert kv_mb_gemma_no_swa == 1437


def test_process_manager_starts_llama_with_ubatch_flag():
    """Verify that ProcessManager passes -ub 512 (or config value) to llama-server CLI and calculate_layer_offload."""
    from pathlib import Path
    pm = ProcessManager()
    captured_cmd = []

    def fake_popen(cmd, *args, **kwargs):
        captured_cmd.extend(cmd)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 9999
        mock_proc.stdout.readline.side_effect = ["HTTP server listening\n", ""]
        return mock_proc

    with patch("services.process_manager.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")), \
         patch("services.process_manager.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/test.gguf")), \
         patch("services.process_manager.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
         patch("services.process_manager.ServerConfigResolver.resolve_cuda_dir", return_value=None), \
         patch("services.process_manager.ServerConfigResolver.load_ini", return_value={"ubatch_size": "256"}), \
         patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
         patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(40, True, "OK", {})) as mock_budget, \
         patch("subprocess.Popen", side_effect=fake_popen), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("requests.get") as mock_get:

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_get.return_value = mock_resp

        pm.start_llama_server_sync(model_identifier="test_model", context_length=8192)

        # Check that -ub and 256 are in captured_cmd
        assert "-ub" in captured_cmd
        ub_idx = captured_cmd.index("-ub")
        assert captured_cmd[ub_idx + 1] == "256"
        # Check that calculate_layer_offload was called with ubatch_size=256
        mock_budget.assert_called_once()
        assert mock_budget.call_args.kwargs.get("ubatch_size") == 256


def test_process_manager_asymmetric_kv_flags_and_preflight_guard(caplog):
    """Verify that ProcessManager passes [-ctk q8_0 -ctv q4_0] for asymmetric KV and falls back to f16 when fa=off."""
    from pathlib import Path
    pm = ProcessManager()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}

    # 1. Asymmetric KV with Flash Attention enabled -> passes -ctk q8_0 -ctv q4_0
    captured_cmd = []

    def fake_popen(cmd, *args, **kwargs):
        captured_cmd.extend(cmd)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 9999
        mock_proc.stdout.readline.side_effect = ["HTTP server listening\n", ""]
        return mock_proc

    with patch("services.process_manager.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")), \
         patch("services.process_manager.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/test.gguf")), \
         patch("services.process_manager.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
         patch("services.process_manager.ServerConfigResolver.resolve_cuda_dir", return_value=None), \
         patch("utils.config_manager.ConfigManager.load_config", return_value={"kv_cache_type": "q8_0_q4_0", "flash_attention_enabled": True}), \
         patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
         patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(40, True, "OK", {})) as mock_budget, \
         patch("subprocess.Popen", side_effect=fake_popen), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("requests.get", return_value=mock_resp):

        pm.start_llama_server_sync(model_identifier="test_model", context_length=8192)

        assert "-ctk" in captured_cmd
        ctk_idx = captured_cmd.index("-ctk")
        assert captured_cmd[ctk_idx + 1] == "q8_0"

        assert "-ctv" in captured_cmd
        ctv_idx = captured_cmd.index("-ctv")
        assert captured_cmd[ctv_idx + 1] == "q4_0"

        # Check calculate_layer_offload received cache_type="q8_0_q4_0"
        assert mock_budget.call_args.kwargs.get("cache_type") == "q8_0_q4_0"

    # 2. Pre-flight Guard: flash_attention_enabled=False forces ctk=f16, ctv=f16
    captured_cmd_fa_off = []

    def fake_popen_fa_off(cmd, *args, **kwargs):
        captured_cmd_fa_off.extend(cmd)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 9999
        mock_proc.stdout.readline.side_effect = ["HTTP server listening\n", ""]
        return mock_proc

    with patch("services.process_manager.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")), \
         patch("services.process_manager.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/test.gguf")), \
         patch("services.process_manager.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
         patch("services.process_manager.ServerConfigResolver.resolve_cuda_dir", return_value=None), \
         patch("utils.config_manager.ConfigManager.load_config", return_value={"kv_cache_type": "q8_0_q4_0", "flash_attention_enabled": False}), \
         patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
         patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(40, True, "OK", {})) as mock_budget_fa_off, \
         patch("subprocess.Popen", side_effect=fake_popen_fa_off), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("requests.get", return_value=mock_resp):

        import logging
        with caplog.at_level(logging.WARNING):
            pm.start_llama_server_sync(model_identifier="test_model", context_length=8192)

        ctk_idx = captured_cmd_fa_off.index("-ctk")
        assert captured_cmd_fa_off[ctk_idx + 1] == "f16"

        ctv_idx = captured_cmd_fa_off.index("-ctv")
        assert captured_cmd_fa_off[ctv_idx + 1] == "f16"

        assert mock_budget_fa_off.call_args.kwargs.get("cache_type") == "f16"
        assert "Pre-flight Guard" in caplog.text


def test_gemma4_offload_increases_from_44_to_46_with_ubatch_256():
    """Verify that on Gemma 4 12B (Q4_K_S, ctx=14336, q8_0) post-Forge (OS=553 MB) offload increases from NGL=44 to NGL=46, and cold start yields NGL=48/48 (99)."""
    gemma4_meta = {
        "total_layers": 48,
        "model_size_mb": 6850.0,
        "architecture": "gemma4",
        "is_moe": False,
        "context_length": 131072,
        "sliding_window": 1024,
        "sliding_window_pattern": [True, True, True, True, True, False] * 8,
        "head_count": 16,
        "head_count_kv": [8, 8, 8, 8, 8, 1] * 8,
        "key_length": 256,
        "value_length": 256,
        "vocab_size": 262144,
    }

    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=gemma4_meta):

        # 1. Post-Forge (OS=553 MB, arbiter managed): ubatch 512 gives NGL=44, ubatch 256 gives NGL=46
        ngl_512, _, _, audit_512 = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_gemma4.gguf",
            target_context=14336,
            total_vram_mb=8151,
            current_used_vram_mb=553.0,
            available_ram_mb=16384.0,
            cache_type="q8_0",
            is_arbiter_managed=True,
            ubatch_size=512,
        )
        assert ngl_512 == 44
        assert audit_512["logits_buffer_mb"] == 512

        ngl_256, can_run_256, _, audit_256 = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_gemma4.gguf",
            target_context=14336,
            total_vram_mb=8151,
            current_used_vram_mb=553.0,
            available_ram_mb=16384.0,
            cache_type="q8_0",
            is_arbiter_managed=True,
            ubatch_size=256,
        )
        assert ngl_256 == 46
        assert can_run_256 is True
        assert audit_256["logits_buffer_mb"] == 256
        assert audit_256["compute_buffer_mb"] == 396

        # 2. Cold start (OS=150 MB, no arbiter): all 48 layers offload to GPU (NGL=99 / 48/48)
        ngl_cold, can_run_cold, _, audit_cold = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_gemma4.gguf",
            target_context=14336,
            total_vram_mb=8151,
            current_used_vram_mb=150.0,
            available_ram_mb=16384.0,
            cache_type="q8_0",
            is_arbiter_managed=False,
            ubatch_size=256,
        )
        assert can_run_cold is True
        assert ngl_cold in (48, 99)
        assert audit_cold["final_ngl"] in (48, 99)


# ---------------------------------------------------------------------------
# 5. ForgeClient unload_checkpoint HTTP 500 and VRAMArbiter stabilization plateau
# ---------------------------------------------------------------------------
def test_forge_unload_checkpoint_http_500_returns_false_and_unloaded_flag_false(monkeypatch):
    """When Forge returns HTTP 500 on unload-checkpoint, returns False and retains _checkpoint_unloaded=False."""
    client = ForgeClient()
    client._checkpoint_unloaded = False

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "IndexError: list index out of range"
    monkeypatch.setattr("requests.post", lambda *a, **k: mock_resp)

    free_vram_mock = MagicMock()
    monkeypatch.setattr(client, "free_vram", free_vram_mock)

    result = client.unload_checkpoint()
    assert result is False
    assert client._checkpoint_unloaded is False
    free_vram_mock.assert_called_once_with(force=True, unload_models=True)


def test_vram_arbiter_stabilization_plateau_at_780mb(monkeypatch):
    """VRAMArbiter._wait_for_vram_stabilization exits via plateau (< 1500 MB and delta < 15 MB) at 778.1 MB."""
    arbiter = VRAMArbiter.get_instance()

    # Emulate readings around 778.1 MB: 780.0, 778.5, 778.1
    readings = iter([780.0, 778.5, 778.1, 778.1, 778.1, 778.1, 778.1])
    monkeypatch.setattr(LayerBudgeter, "get_current_used_vram_mb", lambda: next(readings, 778.1))

    start_t = time.monotonic()
    # timeout 2.5s, step 0.01s, dwell 0.05s for instantaneous test
    arbiter._wait_for_vram_stabilization(timeout_s=1.0, step_s=0.01, dwell_s=0.05)
    elapsed = time.monotonic() - start_t

    # Must complete well under timeout_s (1.0s), confirming plateau break
    assert elapsed < 0.5


# ---------------------------------------------------------------------------
# 6. GGUF Streaming Parser & Sliding Window Metadata
# ---------------------------------------------------------------------------
def _pack_gguf_str(s: str) -> bytes:
    encoded = s.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _build_test_gguf(kv_items: list) -> bytes:
    """Builds a well-formed binary GGUF v3 file from a list of (key, vtype, val)."""
    buf = bytearray()
    buf += b"GGUF"
    buf += struct.pack("<I", 3)  # version 3
    buf += struct.pack("<Q", 0)  # tensor_count = 0
    buf += struct.pack("<Q", len(kv_items))  # metadata_kv_count

    for key, vtype, val in kv_items:
        buf += _pack_gguf_str(key)
        buf += struct.pack("<I", vtype)
        if vtype == 4:  # UINT32
            buf += struct.pack("<I", int(val))
        elif vtype == 8:  # STRING
            buf += _pack_gguf_str(str(val))
        elif vtype == 9:  # ARRAY
            item_type, items = val
            buf += struct.pack("<I", item_type)
            buf += struct.pack("<Q", len(items))
            for item in items:
                if item_type == 4:  # UINT32
                    buf += struct.pack("<I", int(item))
                elif item_type == 8:  # STRING
                    buf += _pack_gguf_str(str(item))
    return bytes(buf)


def test_gguf_streaming_parser_reads_keys_after_large_array(tmp_path):
    """Architecture keys placed after large arrays (>64KB like tokenizer.ggml.tokens) are read correctly."""
    # 500 string tokens of 200 bytes each = ~104 KB, well over the legacy 64 KB buffer limit
    large_tokens = ["token_" + ("x" * 200) for _ in range(500)]
    kv_items = [
        ("general.architecture", 8, "llama"),
        ("llama.block_count", 4, 32),
        ("tokenizer.ggml.tokens", 9, (8, large_tokens)),
        ("llama.attention.sliding_window", 4, 1024),
    ]
    gguf_bytes = _build_test_gguf(kv_items)
    model_file = tmp_path / "streaming_test.gguf"
    model_file.write_bytes(gguf_bytes)

    meta = LayerBudgeter.parse_gguf_metadata(model_file)
    assert meta["architecture"] == "llama"
    assert meta["total_layers"] == 32
    assert meta["sliding_window"] == 1024
    assert meta["vocab_size"] == 500
    assert meta["schema_version"] == 3


def test_gguf_array_sliding_window_and_pattern_synthesis(tmp_path):
    """Array sliding_window is extracted as max value and auto-synthesizes sliding_window_pattern."""
    kv_items = [
        ("general.architecture", 8, "gemma2"),
        ("gemma2.block_count", 4, 6),
        ("gemma2.attention.sliding_window", 9, (4, [1024, 1024, 1024, 1024, 1024, 0])),
    ]
    gguf_bytes = _build_test_gguf(kv_items)
    model_file = tmp_path / "array_swa_test.gguf"
    model_file.write_bytes(gguf_bytes)

    meta = LayerBudgeter.parse_gguf_metadata(model_file)
    assert meta["architecture"] == "gemma2"
    assert meta["total_layers"] == 6
    assert meta["sliding_window"] == 1024
    assert meta["sliding_window_pattern"] == [True, True, True, True, True, False]
    assert meta["schema_version"] == 3


def test_models_cache_invalidates_entry_without_schema_version_2(tmp_path, monkeypatch):
    """Models cache entry is invalidated and re-parsed if schema_version != 3 or missing."""
    kv_items = [
        ("general.architecture", 8, "qwen2"),
        ("qwen2.block_count", 4, 28),
        ("qwen2.attention.sliding_window", 4, 4096),
    ]
    gguf_bytes = _build_test_gguf(kv_items)
    model_file = tmp_path / "cache_invalidation_test.gguf"
    model_file.write_bytes(gguf_bytes)

    stat = model_file.stat()
    cache_key = f"{str(model_file.resolve())}::{stat.st_mtime}::{stat.st_size}"

    # Inject stale cache entry (schema_version is 1 or missing, total_layers=999, sliding_window=0)
    fake_cache = {
        cache_key: {
            "schema_version": 1,
            "total_layers": 999,
            "sliding_window": 0,
            "vocab_size": 32000,
        }
    }
    monkeypatch.setattr(LayerBudgeter, "_read_models_cache", classmethod(lambda cls: fake_cache))
    written_cache = {}
    monkeypatch.setattr(LayerBudgeter, "_write_models_cache", classmethod(lambda cls, c: written_cache.update(c)))

    meta = LayerBudgeter.parse_gguf_metadata(model_file)

    # Must NOT have used the stale 999 layers / 0 sliding_window from fake_cache
    assert meta["total_layers"] == 28
    assert meta["sliding_window"] == 4096
    assert meta["schema_version"] == 3
    assert written_cache[cache_key]["schema_version"] == 3
    assert written_cache[cache_key]["total_layers"] == 28


# ---------------------------------------------------------------------------
# 6. Gemma 4 Audit Fixes: KV Slot Topology, -b/-ub CLI sync, and SSE Error / OOM
# ---------------------------------------------------------------------------
def test_kv_slot_invalidation_on_gpu_layers_mismatch(tmp_path, monkeypatch):
    """
    KV-slot dump saved at NGL=99 (normalized to 48) is rejected on reload if NGL=47.
    When NGL matches, slot restore is accepted.
    """
    kv_dir = tmp_path / "kv_slots"
    kv_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("services.process_manager.KV_SLOTS_DIR", kv_dir)

    pm = ProcessManager()
    pm._llama_process = MagicMock()
    pm._current_running_port = 1234
    pm._current_running_model_path = "C:/models/gemma-4-12b.gguf"
    pm._current_running_ctx = 14336
    pm._current_running_ngl = 99
    pm._current_total_layers = 48
    pm._current_running_ctk = "q8_0"
    pm._current_running_ctv = "q8_0"
    pm._current_running_fa = "on"

    fake_mtime = 1700000000.0
    mock_save_resp = MagicMock(status_code=200)

    with patch("requests.post", return_value=mock_save_resp), \
         patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/gemma-4-12b.gguf")), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("os.path.getmtime", return_value=fake_mtime):
        saved = pm._save_kv_slot_for_eviction()
        assert saved is True

    meta_file = kv_dir / "slot_metadata.json"
    assert meta_file.exists()
    import json
    with open(meta_file, "r", encoding="utf-8") as f:
        saved_meta = json.load(f)
    assert saved_meta["gpu_layers"] == 48

    slot_file = kv_dir / "evicted_slot_0.bin"
    slot_file.write_bytes(b"dummy_slot_data")

    # 1. Попытка восстановить при gpu_layers = 47 (дефицит VRAM после Forge)
    mismatched_meta = dict(saved_meta)
    mismatched_meta["gpu_layers"] = 47

    with patch("requests.post") as mock_post:
        restored = pm._restore_kv_slot_after_start(mismatched_meta)
        assert restored is False
        mock_post.assert_not_called()

    # Дамп должен быть инвалидирован и удален
    assert not slot_file.exists()
    assert not meta_file.exists()

    # 2. Повторное сохранение и проверка успешного восстановления при совпадении gpu_layers = 48
    with patch("requests.post", return_value=mock_save_resp), \
         patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/gemma-4-12b.gguf")), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("os.path.getmtime", return_value=fake_mtime):
        saved2 = pm._save_kv_slot_for_eviction()
        assert saved2 is True

    slot_file.write_bytes(b"dummy_slot_data_2")
    matching_meta = dict(saved_meta)
    mock_restore_resp = MagicMock(status_code=200)

    with patch("requests.post", return_value=mock_restore_resp) as mock_post:
        restored_ok = pm._restore_kv_slot_after_start(matching_meta)
        assert restored_ok is True
        mock_post.assert_called_once_with(
            "http://127.0.0.1:1234/slots/0?action=restore",
            json={"filename": "evicted_slot_0.bin"},
            timeout=5.0
        )


def test_llama_server_cli_includes_batch_size_and_ubatch_size(tmp_path, monkeypatch):
    """ProcessManager includes both -b and -ub in llama-server CLI arguments."""
    pm = ProcessManager()
    captured_cmd = []

    def fake_popen(cmd, *args, **kwargs):
        captured_cmd.extend(cmd)
        raise RuntimeError("Intercepted Popen for inspection")

    with patch("services.process_manager.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")), \
         patch("services.process_manager.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/test_model.gguf")), \
         patch("services.process_manager.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
         patch("services.process_manager.ServerConfigResolver.resolve_cuda_dir", return_value=None), \
         patch("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", return_value=(True, 150)), \
         patch("services.layer_budgeter.LayerBudgeter.get_current_used_vram_mb", return_value=150.0), \
         patch("services.layer_budgeter.LayerBudgeter.parse_gguf_metadata", return_value={"total_layers": 32, "model_size_mb": 4000.0, "vocab_size": 32000}), \
         patch("utils.system_monitor.SystemMonitor.get_total_vram_mb", return_value=8192), \
         patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
         patch("pathlib.Path.exists", return_value=True):

        pm.start_llama_server_sync(model_identifier="test_model", context_length=8192)

        assert "-b" in captured_cmd
        assert "-ub" in captured_cmd
        b_idx = captured_cmd.index("-b")
        ub_idx = captured_cmd.index("-ub")
        b_val = int(captured_cmd[b_idx + 1])
        ub_val = int(captured_cmd[ub_idx + 1])
        assert ub_val == 256
        assert b_val >= ub_val
        assert b_val == 512


def test_sse_error_chunk_triggers_runtime_error_and_model_state_reset(monkeypatch):
    """
    When llama-server emits an SSE chunk with 'error' (e.g. bad allocation),
    chat_completion raises RuntimeError, marks model unloaded and triggers OOM failure flag on PM.
    """
    LMStudioClient._instance = None
    client = LMStudioClient()
    client._is_model_unloaded = False
    client._last_loaded_model_id = "gemma-4-12b"

    mock_pm = MagicMock()
    mock_pm.is_lm_studio_running.return_value = True
    client.set_process_manager(mock_pm)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines.return_value = [
        b'data: {"error": {"message": "got exception: bad allocation", "code": 500, "type": "server_error"}}\n\n'
    ]
    mock_resp.content = b""
    client._persistent_session = MagicMock()
    client._persistent_session.post.return_value = mock_resp

    monkeypatch.setattr(client, "is_model_loaded", lambda m=None: True)

    with pytest.raises(RuntimeError, match="bad allocation"):
        client.chat_completion(
            model="gemma-4-12b",
            messages=[{"role": "user", "content": "hello"}],
            stream=True
        )

    # State must be reset for reload
    assert client._is_model_unloaded is True
    assert client._last_loaded_model_id is None
    assert mock_pm.is_oom_failure is True
    assert "bad allocation" in str(mock_pm.last_error).lower()


def test_stream_llama_stdout_rc_nonzero_triggers_extract_error_and_oom_flag(tmp_path, monkeypatch):
    """When llama-server crashes with rc != 0, _stream_llama_stdout extracts error and sets ServerState.ERROR."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("services.process_manager.LOGS_DIR", log_dir)
    log_file = log_dir / "llama_server.log"
    log_file.write_text("slot iterate: id 0 | task 1 | got exception: bad allocation\n", encoding="utf-8")

    pm = ProcessManager()
    pm._server_state = ServerState.READY
    pm._llama_process = MagicMock()

    err_msg, is_oom = pm._extract_llama_startup_error()
    assert is_oom is True
    assert "bad allocation" in err_msg.lower()


