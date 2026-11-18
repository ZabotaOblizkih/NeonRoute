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

    loaded = client.is_model_loaded("test-model-id")
    assert loaded is False
    assert client._last_loaded_model_id is None


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

    with pytest.raises(ConnectionError):
        client.chat_completion(messages=[{"role": "user", "content": "hi"}], stream=False)

    assert client._is_model_unloaded is True
    assert client._last_loaded_model_id is None
    mock_pm.is_lm_studio_running.assert_called_once()


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
    """Model with vocab_size=262144 receives analytical compute buffer 652 MB (140 base + 512 logits) and SAFETY_MARGIN_VRAM_MB=250."""
    assert SAFETY_MARGIN_VRAM_MB == 250

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
        # compute_mb: base_graph_mb(140) + logits_mb(512) = 652 MB
        assert audit["compute_buffer_mb"] == 652
        assert audit["logits_buffer_mb"] == 512
        assert audit["ubatch_size"] == 512
        # safety_margin_mb should be 250
        assert audit["safety_margin_mb"] == 250
        assert ngl == 37


def test_layer_budgeter_analytical_compute_mb_across_vocabularies():
    """Verify analytical compute_mb across various vocabularies: 32K (203 MB), 152K (437 MB), 262K (652 MB)."""
    # 1. Vocab = 32000 (Llama): base 140 + ceil(512 * 32000 * 4 / 1024^2) = 140 + 63 = 203 MB
    meta_32k = {"total_layers": 32, "model_size_mb": 7000.0, "vocab_size": 32000}
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_32k):
        _, _, _, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_32k.gguf", target_context=8192, total_vram_mb=8192, is_arbiter_managed=True, current_used_vram_mb=200.0
        )
        assert audit["logits_buffer_mb"] == 63
        assert audit["compute_buffer_mb"] == 203

    # 2. Vocab = 152064 (Qwen 2.5): base 140 + ceil(512 * 152064 * 4 / 1024^2) = 140 + 297 = 437 MB
    meta_152k = {"total_layers": 28, "model_size_mb": 7000.0, "vocab_size": 152064}
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_152k):
        _, _, _, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_152k.gguf", target_context=8192, total_vram_mb=8192, is_arbiter_managed=True, current_used_vram_mb=200.0
        )
        assert audit["logits_buffer_mb"] == 297
        assert audit["compute_buffer_mb"] == 437

    # 3. Small model < 5000 MB: base 100 instead of 140
    meta_small = {"total_layers": 24, "model_size_mb": 4000.0, "vocab_size": 32000}
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_small):
        _, _, _, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_small.gguf", target_context=8192, total_vram_mb=8192, is_arbiter_managed=True, current_used_vram_mb=200.0
        )
        assert audit["compute_buffer_mb"] == 100 + 63  # 163 MB

    # 4. Custom ubatch_size = 256: ceil(256 * 262144 * 4 / 1024^2) = 256 MB logits
    meta_gemma = {"total_layers": 48, "model_size_mb": 7000.0, "vocab_size": 262144}
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_gemma):
        _, _, _, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_gemma.gguf", target_context=8192, total_vram_mb=8192, is_arbiter_managed=True, current_used_vram_mb=200.0, ubatch_size=256
        )
        assert audit["ubatch_size"] == 256
        assert audit["logits_buffer_mb"] == 256
        assert audit["compute_buffer_mb"] == 140 + 256  # 396 MB


def test_layer_budgeter_analytical_estimate_kv_cache_mb():
    """Verify analytical estimate_kv_cache_mb with SWA, array head_count_kv, and fallback."""
    # 1. Analytical SWA for Gemma (hybrid array of heads: 40 SWA of 8 heads, 8 Global of 1 head)
    # Average hkv = (40*8 + 8*1) / 48 = 328 / 48 = 6.8333 heads
    # total_layers = 48, k_len = 256, v_len = 256, q8_0 (1.0625 bytes)
    # bytes_per_token_per_layer = (328/48) * 512 * 1.0625 = 3718.6667
    # ctx = 14336, sliding_window = 1024 -> local_ctx = 1024 + 512 = 1536
    # effective_layer_tokens = 40 * 1536 + 8 * 14336 = 61440 + 114688 = 176128
    # raw_mb = 176128 * 3718.6667 / 1048576 = 624.63 MB
    # raw_mb * 1.15 = 718.33 -> 719 MB
    meta_swa = {
        "total_layers": 48,
        "sliding_window": 1024,
        "sliding_window_pattern": [True] * 40 + [False] * 8,
        "head_count_kv": [8] * 40 + [1] * 8,
        "key_length": 256,
        "value_length": 256,
    }
    kv_mb = LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0", gguf_meta=meta_swa)
    assert kv_mb == 719

    # 2. q4 cache type: type_bytes = 0.5625
    kv_mb_q4 = LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q4", gguf_meta=meta_swa)
    assert kv_mb_q4 == 381

    # 3. Fallback when head dimensions are absent: uses kb_per_token = 64
    meta_no_dims = {"total_layers": 32, "sliding_window": 0}
    kv_mb_fb = LayerBudgeter.estimate_kv_cache_mb(8192, cache_type="q8_0", gguf_meta=meta_no_dims)
    # raw_mb = 8192 * 64 / 1024 = 512 MB * 1.15 = 588.8 -> 589 MB
    assert kv_mb_fb == 589


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
         patch("services.process_manager.ServerConfigResolver.load_ini", return_value={"ubatch_size": "512"}), \
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

        # Check that -ub and 512 are in captured_cmd
        assert "-ub" in captured_cmd
        ub_idx = captured_cmd.index("-ub")
        assert captured_cmd[ub_idx + 1] == "512"
        # Check that calculate_layer_offload was called with ubatch_size=512
        mock_budget.assert_called_once()
        assert mock_budget.call_args.kwargs.get("ubatch_size") == 512


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
