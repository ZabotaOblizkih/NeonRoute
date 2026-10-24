"""
Unit tests for LMStudioClient headless integration (Step 4).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.lm_studio import LMStudioClient, ModelNotFoundError


@pytest.fixture(autouse=True)
def reset_client_state():
    """Resets singleton state between tests."""
    client = LMStudioClient()
    client._is_model_unloaded = False
    client._last_loaded_model_id = None
    client._current_loaded_ctx = 8192
    client._current_loaded_gpu = "1.0"
    client.process_manager = None
    yield client


def test_set_process_manager(reset_client_state):
    client = reset_client_state
    mock_pm = MagicMock()
    client.set_process_manager(mock_pm)
    assert client.process_manager is mock_pm


def test_is_model_loaded_checks(reset_client_state):
    client = reset_client_state
    client._last_loaded_model_id = "qwen2.5-coder-7b-instruct"

    # Exact match
    assert client.is_model_loaded("qwen2.5-coder-7b-instruct") is True

    # Stem match with .gguf and path
    assert client.is_model_loaded("qwen2.5-coder-7b-instruct.gguf") is True
    assert client.is_model_loaded("C:/models/qwen2.5-coder-7b-instruct.gguf") is True
    assert client.is_model_loaded("Qwen/qwen2.5-coder-7b-instruct") is True

    # Different model
    assert client.is_model_loaded("l3-8b-stheno-v3.2") is False

    # When unloaded, must always return False
    client._is_model_unloaded = True
    assert client.is_model_loaded("qwen2.5-coder-7b-instruct") is False


def test_unload_models_cli_headless_preserves_last_loaded_model_id(reset_client_state):
    client = reset_client_state
    client._last_loaded_model_id = "model_to_restore"
    client._current_loaded_ctx = 14336
    client._current_loaded_gpu = "1.0"

    mock_pm = MagicMock()
    mock_pm.has_headless_server.return_value = True
    client.set_process_manager(mock_pm)

    success = client.unload_models_cli()

    assert success is True
    assert client._is_model_unloaded is True
    assert client._current_loaded_ctx == 0
    # Must STRICTLY preserve last loaded model ID for auto-restore after Forge
    assert client._last_loaded_model_id == "model_to_restore"
    mock_pm.evict_llm_for_forge_sync.assert_called_once()


def test_ensure_model_loaded_fast_path(reset_client_state):
    client = reset_client_state
    client._last_loaded_model_id = "active_model"
    client._is_model_unloaded = False
    client._current_loaded_ctx = 14336
    client._current_loaded_gpu = "1.0"

    with patch.object(client, "check_health", return_value=(True, "Online")):
        # Requesting same model with smaller or equal context
        res = client.ensure_model_loaded("active_model", context_length=8192)
        assert res is True


def test_ensure_model_loaded_headless_delegates_to_process_manager(reset_client_state):
    client = reset_client_state
    mock_pm = MagicMock()
    mock_pm.has_headless_server.return_value = True
    mock_pm.start_llama_server_sync.return_value = True
    client.set_process_manager(mock_pm)

    with patch("services.server_config.ServerConfigResolver.load_ini", return_value={"gpu_layers": 99}):
        ok = client.ensure_model_loaded("new_model", context_length=14336, gpu_ratio="1.0")

    assert ok is True
    assert client._is_model_unloaded is False
    assert client._last_loaded_model_id == "new_model"
    assert client._current_loaded_ctx == 14336
    assert client._current_loaded_gpu == "1.0"

    mock_pm.start_llama_server_sync.assert_called_once_with(
        model_identifier="new_model",
        context_length=14336,
        gpu_layers=99
    )


def test_ensure_model_loaded_headless_cpu_only(reset_client_state):
    client = reset_client_state
    mock_pm = MagicMock()
    mock_pm.has_headless_server.return_value = True
    mock_pm.start_llama_server_sync.return_value = True
    client.set_process_manager(mock_pm)

    client.ensure_model_loaded("cpu_model", context_length=8192, gpu_ratio="cpu")
    mock_pm.start_llama_server_sync.assert_called_once_with(
        model_identifier="cpu_model",
        context_length=8192,
        gpu_layers=0
    )


def test_ensure_model_loaded_headless_failure_raises_model_not_found(reset_client_state):
    client = reset_client_state
    mock_pm = MagicMock()
    mock_pm.has_headless_server.return_value = True
    mock_pm.start_llama_server_sync.return_value = False
    client.set_process_manager(mock_pm)

    with pytest.raises(ModelNotFoundError):
        client.ensure_model_loaded("missing_model")


def test_full_forge_eviction_and_restore_cycle(reset_client_state):
    """
    Simulates the full VRAM cycle:
    1. LLM loaded with Model A
    2. Forge generation triggers unload_models_cli() -> evicts LLM
    3. Forge completes and calls ensure_model_loaded() without model name
    4. Client restores Model A automatically
    """
    client = reset_client_state
    mock_pm = MagicMock()
    mock_pm.has_headless_server.return_value = True
    mock_pm.start_llama_server_sync.return_value = True
    client.set_process_manager(mock_pm)

    # 1. Initial load
    client.ensure_model_loaded("model_alpha", context_length=14336)
    assert client._last_loaded_model_id == "model_alpha"
    assert client._is_model_unloaded is False

    # 2. Forge eviction
    client.unload_models_cli()
    assert client._is_model_unloaded is True
    assert client._last_loaded_model_id == "model_alpha"
    mock_pm.evict_llm_for_forge_sync.assert_called_once()

    # 3. Restoration after Forge
    client.ensure_model_loaded()
    assert client._is_model_unloaded is False
    assert client._last_loaded_model_id == "model_alpha"

    # start_llama_server_sync called twice: 1 for initial load, 1 for restoration
    assert mock_pm.start_llama_server_sync.call_count == 2
    mock_pm.start_llama_server_sync.assert_called_with(
        model_identifier="model_alpha",
        context_length=14336,
        gpu_layers=99
    )
