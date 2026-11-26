"""
Unit tests for services/lm_studio.py.
Covers:
- Standalone functions and errors: ModelNotFoundError, _get_vram_telemetry_str, _is_rtx_gpu_available
- Singleton lifecycle and state management
- Health checks and model discovery (GET /models)
- Model loading state, stem matching, and reloading guards
- Subprocess/headless server coordination (ensure_model_loaded, unload_models_cli)
- OpenAI-compatible chat completion payload construction:
  - messages, temperature, max_tokens, stop
  - sampling parameters: top_p, min_p, repetition_penalty, response_format
  - advanced samplers: DRY (dry_multiplier, etc.), XTC (xtc_probability, etc.)
- Server-Sent Events (SSE) streaming, reasoning tags (<think>...</think>), [DONE] marker
- Non-streaming completion fallback and usage statistics
- Fault tolerance: ConnectionRefusedError, HTTP 400 (vision/multimodal, jinja role alternation),
  Model not loaded errors, user interruption, malformed stream chunks.
100% offline, isolated from network, GPU, and external processes.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.lm_studio import (
    LMStudioClient,
    ModelNotFoundError,
    LlamaServerStartupError,
    _get_vram_telemetry_str,
    _is_rtx_gpu_available,
)


@pytest.fixture(autouse=True)
def airgap_network_and_sleep(monkeypatch):
    """Guarantees 100% offline airgap and sub-second execution."""
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def _default_resp(*args, **kwargs):
        r = MagicMock(spec=requests.Response)
        r.status_code = 200
        r.text = "{}"
        r.content = b"{}"
        r.json.return_value = {"data": []}
        r.iter_lines.return_value = []
        return r

    monkeypatch.setattr("requests.get", _default_resp)
    monkeypatch.setattr("requests.post", _default_resp)
    monkeypatch.setattr("requests.Session.get", _default_resp)
    monkeypatch.setattr("requests.Session.post", _default_resp)


@pytest.fixture
def lms_client():
    """Provides a cleanly reset LMStudioClient singleton instance."""
    LMStudioClient._instance = None
    client = LMStudioClient(base_url="http://127.0.0.1:1234/v1")
    client._is_aborted = False
    client._is_model_unloaded = False
    client._current_ctx = 0
    client._current_loaded_ctx = 8192
    client._current_loaded_gpu = "1.0"
    client._last_loaded_model_id = "test-model"
    client.process_manager = None
    return client


# =========================================================================
# 1. Standalone Functions & Custom Errors
# =========================================================================

class TestHelperFunctionsAndErrors:
    def test_model_not_found_error(self):
        err = ModelNotFoundError("qwen-2.5-7b")
        assert err.model_name == "qwen-2.5-7b"
        assert "qwen-2.5-7b" in str(err)
        assert isinstance(err, RuntimeError)

    def test_get_vram_telemetry_str_success(self, monkeypatch):
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlDeviceGetMemoryInfo.return_value = MagicMock(
            free=4096 * 1024 * 1024,
            used=2048 * 1024 * 1024
        )
        monkeypatch.setattr("services.lm_studio.pynvml", mock_pynvml, raising=False)
        with patch.dict("sys.modules", {"pynvml": mock_pynvml}):
            telemetry = _get_vram_telemetry_str()
            assert "[VRAM: Free=4096MB, Used=2048MB]" in telemetry

    def test_get_vram_telemetry_str_failure(self):
        with patch.dict("sys.modules", {"pynvml": None}):
            telemetry = _get_vram_telemetry_str()
            assert telemetry == "[VRAM: N/A]"

    @pytest.mark.parametrize("gpu_name,expected", [
        ("NVIDIA GeForce RTX 4070", True),
        (b"NVIDIA GeForce RTX 3080", True),
        ("NVIDIA GeForce GTX 1080", False),
        (None, False),
    ])
    def test_is_rtx_gpu_available(self, gpu_name, expected):
        if gpu_name is None:
            with patch.dict("sys.modules", {"pynvml": None}):
                assert _is_rtx_gpu_available() is expected
        else:
            mock_pynvml = MagicMock()
            mock_pynvml.nvmlDeviceGetName.return_value = gpu_name
            with patch.dict("sys.modules", {"pynvml": mock_pynvml}):
                assert _is_rtx_gpu_available() is expected


# =========================================================================
# 2. Singleton, Initialization & ProcessManager Injection
# =========================================================================

class TestLMStudioClientInitAndSingleton:
    def test_singleton_behavior(self):
        LMStudioClient._instance = None
        c1 = LMStudioClient()
        c2 = LMStudioClient()
        assert c1 is c2
        assert isinstance(c1, LMStudioClient)

    def test_set_process_manager(self, lms_client):
        mock_pm = MagicMock()
        lms_client.set_process_manager(mock_pm)
        assert lms_client.process_manager is mock_pm


# =========================================================================
# 3. Health & Model Discovery (GET /models)
# =========================================================================

class TestHealthAndModelDiscovery:
    @pytest.mark.parametrize("behavior,exp_ok,exp_substr", [
        (MagicMock(status_code=200), True, "LM Studio Online"),
        (MagicMock(status_code=503), False, "HTTP 503"),
        (requests.exceptions.ConnectionError("Connection refused"), False, "LM Studio Offline (Connection Refused)"),
        (TimeoutError("Read timeout"), False, "Offline (Read timeout)"),
    ])
    def test_check_health(self, lms_client, monkeypatch, behavior, exp_ok, exp_substr):
        if isinstance(behavior, Exception):
            monkeypatch.setattr(lms_client._persistent_session, "get", lambda *a, **k: (_ for _ in ()).throw(behavior))
        else:
            monkeypatch.setattr(lms_client._persistent_session, "get", lambda *a, **k: behavior)
        ok, msg = lms_client.check_health()
        assert ok is exp_ok
        assert exp_substr in msg

    def test_get_models(self, lms_client, monkeypatch):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"data": [{"id": "meta-llama-3-8b"}, {"id": "mistral-7b"}]}
        monkeypatch.setattr(lms_client._persistent_session, "get", lambda *a, **k: mock_resp)
        assert lms_client.get_models() == ["meta-llama-3-8b", "mistral-7b"]

        monkeypatch.setattr(lms_client._persistent_session, "get", lambda *a, **k: MagicMock(status_code=500))
        assert lms_client.get_models() == []

    @pytest.mark.parametrize("preferred,expected", [
        ("custom-expert-model", "custom-expert-model"),
        ("default-placeholder", "primary-model"),
    ])
    def test_get_active_model_id_success(self, lms_client, monkeypatch, preferred, expected):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"data": [{"id": "primary-model"}]}
        monkeypatch.setattr(lms_client._persistent_session, "get", lambda *a, **k: mock_resp)
        assert lms_client.get_active_model_id(preferred_model=preferred) == expected

    @pytest.mark.parametrize("behavior,exc_type,exp_substr", [
        (requests.exceptions.ConnectionError(), ConnectionError, "Не удалось подключиться к LM Studio"),
        (MagicMock(status_code=500, text="Internal Server Error"), RuntimeError, "HTTP 500"),
        (MagicMock(status_code=200, json=lambda: {"data": []}), RuntimeError, "не загружена ни одна модель"),
    ])
    def test_get_active_model_id_errors(self, lms_client, monkeypatch, behavior, exc_type, exp_substr):
        if isinstance(behavior, Exception):
            monkeypatch.setattr(lms_client._persistent_session, "get", lambda *a, **k: (_ for _ in ()).throw(behavior))
        else:
            monkeypatch.setattr(lms_client._persistent_session, "get", lambda *a, **k: behavior)
        with pytest.raises(exc_type) as exc:
            lms_client.get_active_model_id()
        assert exp_substr in str(exc.value)


# =========================================================================
# 4. Model Loaded State & Suffix Normalization
# =========================================================================

class TestModelLoadedState:
    @pytest.mark.parametrize("unloaded,last_loaded,query,expected", [
        (True, "any-model", "any-model", False),
        (False, "meta-llama-3-8b-instruct", "meta-llama-3-8b-instruct", True),
        (False, "meta-llama-3-8b-instruct", "META-LLAMA-3-8B-INSTRUCT", True),
        (False, "models/deepseek-coder-6.7b.gguf", "deepseek-coder-6.7b", True),
        (False, "models/deepseek-coder-6.7b.gguf", "deepseek-coder-6.7b-gguf", True),
        (False, "model-alpha", "model-beta", False),
    ])
    def test_is_model_loaded_matching(self, lms_client, unloaded, last_loaded, query, expected):
        lms_client._is_model_unloaded = unloaded
        lms_client._last_loaded_model_id = last_loaded
        assert lms_client.is_model_loaded(query) is expected

    def test_is_model_loaded_queries_models_if_no_last_loaded(self, lms_client, monkeypatch):
        lms_client._last_loaded_model_id = None
        monkeypatch.setattr(lms_client, "get_models", lambda: ["discovered-model-7b"])
        assert lms_client.is_model_loaded("discovered-model-7b") is True
        assert lms_client._last_loaded_model_id == "discovered-model-7b"


# =========================================================================
# 5. Model Unloading & Abort
# =========================================================================

class TestModelUnloadAndAbort:
    def test_abort_closes_active_response(self, lms_client):
        mock_resp = MagicMock()
        lms_client._active_response = mock_resp
        lms_client.abort()
        assert lms_client._is_aborted is True
        mock_resp.close.assert_called_once()

    def test_unload_models_headless_path(self, lms_client):
        mock_pm = MagicMock()
        mock_pm.has_headless_server.return_value = True
        lms_client.set_process_manager(mock_pm)

        res = lms_client.unload_models_cli()
        assert res is True
        assert lms_client._is_model_unloaded is True
        assert lms_client._current_loaded_ctx == 0
        mock_pm.evict_llm_for_forge_sync.assert_called_once()

    def test_unload_models_cli_path(self, lms_client, monkeypatch):
        post_mock = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setattr("requests.post", post_mock)

        mock_subproc = MagicMock()
        mock_subproc.return_value = MagicMock(returncode=0, stdout="All models unloaded", stderr="")
        monkeypatch.setattr("subprocess.run", mock_subproc)

        res = lms_client.unload_models_cli()
        assert res is True
        assert lms_client._is_model_unloaded is True
        mock_subproc.assert_called_once()
        cmd = mock_subproc.call_args[0][0]
        assert cmd == ["lms", "unload", "--all"]

    def test_unload_aliases(self, lms_client, monkeypatch):
        mock_unload = MagicMock(return_value=True)
        monkeypatch.setattr(lms_client, "unload_models_cli", mock_unload)
        assert lms_client.safe_unload_models() is True
        assert lms_client.unload_models() is True
        assert lms_client.unload_model() is True
        assert mock_unload.call_count == 3


# =========================================================================
# 6. Ensure Model Loaded (Headless vs CLI)
# =========================================================================

class TestEnsureModelLoaded:
    def test_ensure_model_loaded_no_available_models_raises(self, lms_client, monkeypatch):
        lms_client._last_loaded_model_id = None
        monkeypatch.setattr(lms_client, "get_models", lambda: [])
        with pytest.raises(ModelNotFoundError):
            lms_client.ensure_model_loaded(preferred_model=None)

    def test_ensure_model_loaded_skip_when_already_loaded_and_sufficient(self, lms_client, monkeypatch):
        lms_client._is_model_unloaded = False
        lms_client._last_loaded_model_id = "qwen-7b"
        lms_client._current_loaded_ctx = 16384
        lms_client._current_loaded_gpu = "1.0"
        monkeypatch.setattr(lms_client, "check_health", lambda: (True, "Online"))
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)

        res = lms_client.ensure_model_loaded(preferred_model="qwen-7b", context_length=8192)
        assert res is True

    def test_ensure_model_loaded_headless_path_success(self, lms_client):
        mock_pm = MagicMock()
        mock_pm.has_headless_server.return_value = True
        mock_pm.start_llama_server_sync.return_value = True
        lms_client.set_process_manager(mock_pm)

        res = lms_client.ensure_model_loaded(preferred_model="llama-3-8b", context_length=8192, gpu_ratio="1.0")
        assert res is True
        assert lms_client._last_loaded_model_id == "llama-3-8b"
        assert lms_client._current_loaded_ctx == 8192
        mock_pm.start_llama_server_sync.assert_called_once()

    def test_ensure_model_loaded_headless_path_failure_raises(self, lms_client, monkeypatch, tmp_path):
        mock_pm = MagicMock()
        mock_pm.has_headless_server.return_value = True
        mock_pm.start_llama_server_sync.return_value = False
        mock_pm.is_oom_failure = False
        mock_pm.last_error = "Server startup failed"
        lms_client.set_process_manager(mock_pm)

        # 1. Если файл существует, но сервер не смог запуститься -> LlamaServerStartupError (RuntimeError)
        dummy_file = tmp_path / "model.gguf"
        dummy_file.write_text("dummy")
        from services.server_config import ServerConfigResolver
        monkeypatch.setattr(ServerConfigResolver, "resolve_model_path", lambda m: dummy_file)

        with pytest.raises(LlamaServerStartupError) as exc_info:
            lms_client.ensure_model_loaded(preferred_model="existing-model")
        assert "Server startup failed" in str(exc_info.value)
        assert isinstance(exc_info.value, RuntimeError)

        # 2. Если файл отсутствует на диске (resolve_model_path -> None) -> ModelNotFoundError
        monkeypatch.setattr(ServerConfigResolver, "resolve_model_path", lambda m: None)
        with pytest.raises(ModelNotFoundError):
            lms_client.ensure_model_loaded(preferred_model="missing-model")

    def test_ensure_model_loaded_cli_path_success(self, lms_client, monkeypatch):
        monkeypatch.setattr(lms_client, "unload_models_cli", lambda: True)
        mock_subproc = MagicMock(return_value=MagicMock(returncode=0, stdout="Loaded successfully", stderr=""))
        monkeypatch.setattr("subprocess.run", mock_subproc)

        res = lms_client.ensure_model_loaded(preferred_model="gemma-2-9b", context_length=8192, gpu_ratio="1.0")
        assert res is True
        assert lms_client._last_loaded_model_id == "gemma-2-9b"
        mock_subproc.assert_called_once()
        cmd = mock_subproc.call_args[0][0]
        assert "load" in cmd
        assert "gemma-2-9b" in cmd
        assert "--context-length" in cmd
        assert "8192" in cmd

    def test_ensure_model_loaded_cli_path_not_found_raises(self, lms_client, monkeypatch):
        monkeypatch.setattr(lms_client, "unload_models_cli", lambda: True)
        mock_subproc = MagicMock(return_value=MagicMock(returncode=1, stdout="Model not found in catalog", stderr=""))
        monkeypatch.setattr("subprocess.run", mock_subproc)

        with pytest.raises(ModelNotFoundError):
            lms_client.ensure_model_loaded(preferred_model="non_existent_model")


# =========================================================================
# 7. Chat Completion, Streaming & Error Handling
# =========================================================================

class TestChatCompletion:
    @staticmethod
    def _patch_session_post(monkeypatch, client, handler):
        def _wrapper(url, *args, **kwargs):
            return handler(url, *args, **kwargs)
        monkeypatch.setattr(client._persistent_session, "post", _wrapper)

    def test_chat_completion_payload_formatting(self, lms_client, monkeypatch):
        captured_payloads = []

        def mock_post(url, *args, **kwargs):
            captured_payloads.append(kwargs.get("json", {}))
            mock_resp = MagicMock(status_code=200)
            mock_resp.iter_lines.return_value = [b'data: {"choices": [{"delta": {"content": "ok"}}]}', b"data: [DONE]"]
            return mock_resp

        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        monkeypatch.setattr(lms_client, "check_health", lambda: (True, "Online"))
        self._patch_session_post(monkeypatch, lms_client, mock_post)

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"}
        ]

        lms_client.chat_completion(
            messages=messages,
            model="test-model",
            temperature=0.85,
            max_tokens=500,
            stop=["</s>", "\n\nUser:"],
            stream=True,
            top_p=0.92,
            min_p=0.05,
            repetition_penalty=1.15,
            dry_multiplier=0.8,
            dry_base=1.75,
            dry_allowed_length=2,
            dry_penalty_last_n=-1,
            xtc_probability=0.5,
            xtc_threshold=0.1,
            response_format={"type": "json_object"}
        )

        assert len(captured_payloads) == 1
        p = captured_payloads[0]
        assert p["model"] == "test-model"
        assert p["messages"] == messages
        assert p["temperature"] == 0.85
        assert p["stream"] is True
        assert p["stop"] == ["</s>", "\n\nUser:"]
        assert p["top_p"] == 0.92
        assert p["min_p"] == 0.05
        assert p["repetition_penalty"] == 1.15
        assert p["dry_multiplier"] == 0.8
        assert p["dry_base"] == 1.75
        assert p["dry_allowed_length"] == 2
        assert p["dry_penalty_last_n"] == -1
        assert p["xtc_probability"] == 0.5
        assert p["xtc_threshold"] == 0.1
        assert p["response_format"] == {"type": "json_object"}

    def test_chat_completion_triggers_reload_when_needed(self, lms_client, monkeypatch):
        lms_client._is_model_unloaded = True
        mock_ensure = MagicMock(return_value=True)
        monkeypatch.setattr(lms_client, "ensure_model_loaded", mock_ensure)

        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = [b'data: {"choices": [{"delta": {"content": "ok"}}]}', b"data: [DONE]"]
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}], model="new-model")
        mock_ensure.assert_called_once()

    def test_chat_completion_streaming_sse_tokens_and_signals(self, lms_client, monkeypatch):
        sse_lines = [
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            b'data: {"choices": [{"delta": {"content": " world"}}]}',
            b'data: {"choices": [{"delta": {"content": "!"}}]}',
            b'data: [DONE]'
        ]
        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = sse_lines
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        streamed_tokens = []
        callbacks_received = []
        lms_client.token_streamed.connect(lambda t: streamed_tokens.append(t))

        result = lms_client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            chunk_callback=lambda piece: callbacks_received.append(piece)
        )

        assert result == "Hello world!"
        assert streamed_tokens == ["Hello", " world", "!"]
        assert callbacks_received == ["Hello", " world", "!"]

    def test_chat_completion_streaming_reasoning_tags(self, lms_client, monkeypatch):
        """Validates that reasoning content is automatically enclosed within <think>...</think>."""
        sse_lines = [
            b'data: {"choices": [{"delta": {"reasoning_content": "Thinking step 1"}}]}',
            b'data: {"choices": [{"delta": {"reasoning_content": " and step 2"}}]}',
            b'data: {"choices": [{"delta": {"content": "Final answer"}}]}',
            b'data: [DONE]'
        ]
        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = sse_lines
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        result = lms_client.chat_completion(messages=[{"role": "user", "content": "solve"}], stream=True)
        assert "<think>" in result
        assert "</think>" in result
        assert "Thinking step 1 and step 2" in result
        assert "Final answer" in result

    def test_reasoning_anti_loop_breaks_on_repetition(self, lms_client, monkeypatch):
        """12+ identical reasoning lines terminate the stream with exactly one </think>."""
        sse_lines = [
            b'data: {"choices": [{"delta": {"reasoning_content": "<|channel>thought\\n"}}]}'
            for _ in range(14)
        ] + [
            b'data: {"choices": [{"delta": {"reasoning_content": "extra thinking after loop\\n"}}]}',
            b'data: [DONE]'
        ]
        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = sse_lines
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        result = lms_client.chat_completion(messages=[{"role": "user", "content": "loop"}], stream=True)
        assert result.count("<think>") == 1
        assert result.count("</think>") == 1
        assert "extra thinking after loop" not in result
        mock_resp.close.assert_called()

    def test_repeated_code_in_content_piece_not_interrupted(self, lms_client, monkeypatch):
        """Repeated code lines in content_piece must NOT be interrupted by the reasoning anti-loop."""
        sse_lines = [
            b'data: {"choices": [{"delta": {"content": "    print(\'loop\')\\n"}}]}'
            for _ in range(20)
        ] + [
            b'data: {"choices": [{"delta": {"content": "main()\\n"}}]}',
            b'data: [DONE]'
        ]
        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = sse_lines
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        result = lms_client.chat_completion(messages=[{"role": "user", "content": "code"}], stream=True)
        assert result.count("print('loop')") == 20
        assert "main()" in result

    def test_chat_completion_streaming_malformed_chunk_tolerance(self, lms_client, monkeypatch):
        """Ensures corrupted SSE JSON chunks are skipped without raising exceptions."""
        sse_lines = [
            b'data: {corrupted_json',
            b'data: {"choices": []}',
            b'data: {"choices": [{"delta": {"content": "valid"}}]}',
            b'data: [DONE]'
        ]
        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = sse_lines
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        res = lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}], stream=True)
        assert res == "valid"

    def test_chat_completion_non_streaming_fallback(self, lms_client, monkeypatch):
        non_stream_json = {
            "choices": [{
                "message": {"content": "Direct response", "reasoning_content": "Chain of thought"},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8}
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = []
        mock_resp.content = json.dumps(non_stream_json).encode("utf-8")
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        res = lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}], stream=False)
        assert "Direct response" in res
        assert "<think>" in res
        assert "Chain of thought" in res

    def test_chat_completion_interrupted_by_user(self, lms_client, monkeypatch):
        def abort_post(url, *a, **k):
            lms_client._is_aborted = True
            return MagicMock(status_code=200)

        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, abort_post)

        with pytest.raises(InterruptedError) as exc:
            lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}])
        assert "прерван пользователем" in str(exc.value)

    def test_chat_completion_http_400_multimodal_vision_error(self, lms_client, monkeypatch):
        errors_emitted = []
        lms_client.error_occurred.connect(lambda msg: errors_emitted.append(msg))

        mock_resp = MagicMock(status_code=400, text="Model does not support vision multimodal images")
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        messages = [{"role": "user", "content": [{"type": "image_url"}]}]
        with pytest.raises(RuntimeError):
            lms_client.chat_completion(messages=messages)

        assert any("Vision-модель" in e for e in errors_emitted)

    def test_chat_completion_http_400_jinja_template_error(self, lms_client, monkeypatch):
        mock_resp = MagicMock(status_code=400, text="Jinja exception: conversation roles must alternate")
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        with pytest.raises(RuntimeError) as exc:
            lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}])
        assert "Вы сменили модель, начните новый диалог" in str(exc.value)

    def test_chat_completion_no_models_loaded_raises_model_not_found_error(self, lms_client, monkeypatch):
        mock_resp = MagicMock(status_code=500, text="No models loaded in LM Studio")
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, lambda url, *a, **k: mock_resp)

        with pytest.raises(ModelNotFoundError):
            lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}])

    def test_chat_completion_connection_error(self, lms_client, monkeypatch):
        errors_emitted = []
        lms_client.error_occurred.connect(lambda msg: errors_emitted.append(msg))

        def _err(*a, **k):
            raise requests.exceptions.ConnectionError("Connection refused on 1234")

        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, _err)

        with pytest.raises(ConnectionError) as exc:
            lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}])
        assert "Не удалось подключиться к LM Studio" in str(exc.value)
        assert len(errors_emitted) > 0

    def test_chat_completion_thinking_mode_payload_chat_template_kwargs(self, lms_client, monkeypatch):
        captured_payloads = []

        def mock_post(url, *args, **kwargs):
            captured_payloads.append(kwargs.get("json", {}))
            mock_resp = MagicMock(status_code=200)
            mock_resp.iter_lines.return_value = [b'data: {"choices": [{"delta": {"content": "ok"}}]}', b"data: [DONE]"]
            return mock_resp

        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, mock_post)

        # enable_thinking=True
        lms_client.chat_completion(
            messages=[{"role": "user", "content": "test thinking"}],
            model="test-model",
            enable_thinking=True
        )
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["chat_template_kwargs"] == {
            "enable_thinking": True,
            "thinking": True
        }

        # enable_thinking=False
        captured_payloads.clear()
        lms_client.chat_completion(
            messages=[{"role": "user", "content": "test thinking"}],
            model="generic-model",
            enable_thinking=False
        )
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["chat_template_kwargs"] == {
            "enable_thinking": False,
            "thinking": False
        }

    def test_chat_completion_qwen3_prefill_when_thinking_false(self, lms_client, monkeypatch):
        captured_payloads = []

        def mock_post(url, *args, **kwargs):
            captured_payloads.append(kwargs.get("json", {}))
            mock_resp = MagicMock(status_code=200)
            mock_resp.iter_lines.return_value = [b'data: {"choices": [{"delta": {"content": "ok"}}]}', b"data: [DONE]"]
            return mock_resp

        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, mock_post)

        input_messages = [{"role": "user", "content": "напиши функцию"}]
        original_copy = list(input_messages)

        lms_client.chat_completion(
            messages=input_messages,
            model="Qwen3.5-35B-A3B-Instruct-GGUF",
            enable_thinking=False
        )

        assert len(captured_payloads) == 1
        sent_messages = captured_payloads[0]["messages"]
        # Проверяем, что в payload добавлен assistant prefill
        assert sent_messages[-1] == {"role": "assistant", "content": "<think>\n\n</think>\n\n"}
        # Проверяем, что исходный список messages НЕ был мутирован
        assert input_messages == original_copy
        assert len(input_messages) == 1

    def test_chat_completion_no_prefill_when_response_format_present(self, lms_client, monkeypatch):
        """Проверяет, что при enable_thinking=False и response_format prefill НЕ добавляется."""
        captured_payloads = []

        def mock_post(url, *args, **kwargs):
            captured_payloads.append(kwargs.get("json", {}))
            mock_resp = MagicMock(status_code=200)
            mock_resp.iter_lines.return_value = [b'data: {"choices": [{"delta": {"content": "ok"}}]}', b"data: [DONE]"]
            return mock_resp

        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, mock_post)

        input_messages = [{"role": "user", "content": "дай json"}]

        lms_client.chat_completion(
            messages=input_messages,
            model="Qwen3.5-35B-A3B-Instruct-GGUF",
            enable_thinking=False,
            response_format={"type": "json_object"}
        )

        assert len(captured_payloads) == 1
        sent_messages = captured_payloads[0]["messages"]
        assert not any(m.get("content") == "<think>\n\n</think>\n\n" for m in sent_messages)

    def test_chat_completion_id_slot_override(self, lms_client, monkeypatch):
        """Проверяет явную передачу id_slot и отключение при id_slot=None."""
        captured_payloads = []

        def mock_post(url, *args, **kwargs):
            captured_payloads.append(kwargs.get("json", {}))
            mock_resp = MagicMock(status_code=200)
            mock_resp.iter_lines.return_value = [b'data: {"choices": [{"delta": {"content": "ok"}}]}', b"data: [DONE]"]
            return mock_resp

        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        self._patch_session_post(monkeypatch, lms_client, mock_post)

        # 1. Явная передача id_slot=None не должна создавать id_slot в payload
        lms_client.chat_completion(
            messages=[{"role": "user", "content": "ping"}],
            model="test-model",
            id_slot=None
        )
        assert len(captured_payloads) == 1
        assert "id_slot" not in captured_payloads[0]

        # 2. Явная передача id_slot=2
        captured_payloads.clear()
        lms_client.chat_completion(
            messages=[{"role": "user", "content": "ping"}],
            model="test-model",
            id_slot=2
        )
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["id_slot"] == 2

    def test_chat_completion_respect_master_cap_option(self, lms_client, monkeypatch):
        """Проверяет снятие среза MASTER_MAX_RESPONSE_TOKENS при respect_master_cap=False."""
        captured_payloads = []

        def mock_post(url, *args, **kwargs):
            captured_payloads.append(kwargs.get("json", {}))
            mock_resp = MagicMock(status_code=200)
            mock_resp.iter_lines.return_value = [b'data: {"choices": [{"delta": {"content": "ok"}}]}', b"data: [DONE]"]
            return mock_resp

        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m: True)
        monkeypatch.setattr(lms_client, "ensure_model_loaded", lambda *a, **k: True)
        lms_client._current_loaded_ctx = 28672
        lms_client._last_loaded_model_id = "test-model"
        self._patch_session_post(monkeypatch, lms_client, mock_post)

        # 1. По умолчанию respect_master_cap=True: запрос 8192 усекается до 4096
        lms_client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
            max_tokens=8192,
            respect_master_cap=True
        )
        assert captured_payloads[-1]["max_tokens"] == 4096

        # 2. При respect_master_cap=False: запрос 8192 передается напрямую (не усекается до 4096)
        lms_client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
            max_tokens=8192,
            respect_master_cap=False
        )
        assert captured_payloads[-1]["max_tokens"] == 8192


class TestAuditRegressionCases:
    """Регрессионные кейсы SSE, dead-process invalidation и OOM ошибок из инцидентов."""

    def test_empty_sse_stream_raises_runtime_error_when_not_aborted(self, lms_client, monkeypatch):
        lms_client._persistent_session = MagicMock()
        mock_resp = MagicMock(status_code=200, content=b"")
        mock_resp.iter_lines.return_value = []
        lms_client._persistent_session.post.return_value = mock_resp
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m=None: True)

        with pytest.raises(RuntimeError, match="LLM stream closed with 0 tokens"):
            lms_client.chat_completion(model="test-model", messages=[{"role": "user", "content": "hello"}], stream=True)

    def test_empty_sse_stream_handles_abort_without_empty_stream_error(self, lms_client, monkeypatch):
        lms_client._persistent_session = MagicMock()
        mock_resp = MagicMock(status_code=200, content=b"")

        def _iter_lines(**kwargs):
            lms_client.abort()
            return []

        mock_resp.iter_lines = _iter_lines
        lms_client._persistent_session.post.return_value = mock_resp
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m=None: True)

        with pytest.raises(InterruptedError):
            lms_client.chat_completion(model="test-model", messages=[{"role": "user", "content": "hello"}], stream=True)

    def test_is_model_loaded_returns_false_and_clears_id_when_process_manager_dead(self, lms_client):
        lms_client._is_model_unloaded = False
        lms_client._last_loaded_model_id = "test-model-id"
        mock_pm = MagicMock()
        mock_pm.is_lm_studio_running.return_value = False
        lms_client.set_process_manager(mock_pm)
        try:
            assert lms_client.is_model_loaded("test-model-id") is False
            assert lms_client._last_loaded_model_id is None
        finally:
            lms_client.set_process_manager(None)

    def test_connection_error_marks_model_unloaded_and_checks_pm(self, lms_client, monkeypatch):
        lms_client._is_model_unloaded = False
        lms_client._last_loaded_model_id = "active-model"
        mock_pm = MagicMock()
        mock_pm.is_lm_studio_running.return_value = False
        lms_client.set_process_manager(mock_pm)

        lms_client._persistent_session = MagicMock()
        lms_client._persistent_session.post.side_effect = requests.exceptions.ConnectionError("Connection refused")
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m=None: True)
        try:
            with pytest.raises(ConnectionError):
                lms_client.chat_completion(messages=[{"role": "user", "content": "hi"}], stream=False)
            assert lms_client._is_model_unloaded is True
            assert lms_client._last_loaded_model_id is None
            mock_pm.is_lm_studio_running.assert_called_once()
        finally:
            lms_client.set_process_manager(None)
            lms_client._current_running_ngl = None
            lms_client._current_total_layers = None

    def test_sse_error_chunk_triggers_runtime_error_and_model_state_reset(self, lms_client, monkeypatch):
        lms_client._is_model_unloaded = False
        lms_client._last_loaded_model_id = "gemma-4-12b"
        mock_pm = MagicMock()
        mock_pm.is_lm_studio_running.return_value = True
        lms_client.set_process_manager(mock_pm)

        mock_resp = MagicMock(status_code=200, content=b"")
        mock_resp.iter_lines.return_value = [
            b'data: {"error": {"message": "got exception: bad allocation", "code": 500, "type": "server_error"}}\n\n'
        ]
        lms_client._persistent_session = MagicMock()
        lms_client._persistent_session.post.return_value = mock_resp
        monkeypatch.setattr(lms_client, "is_model_loaded", lambda m=None: True)

        try:
            with pytest.raises(RuntimeError, match="bad allocation"):
                lms_client.chat_completion(model="gemma-4-12b", messages=[{"role": "user", "content": "hello"}], stream=True)
            assert lms_client._is_model_unloaded is True
            assert lms_client._last_loaded_model_id is None
            assert mock_pm.is_oom_failure is True
            assert "bad allocation" in str(mock_pm.last_error).lower()
        finally:
            lms_client.set_process_manager(None)



