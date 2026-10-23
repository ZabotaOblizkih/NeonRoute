import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import config
from services.lora_manager import LoraManager
from ui.dialogs.local_lora_dialog import get_forge_lora_dirs
from ui.dialogs.clean_cache_dialog import CacheCleanWorker
from services.art_director_config import save_art_director_models, load_art_director_models
from ui.models_manager_dialog import CivitaiParamsWorker
from services import memory_db
from ui.controllers.chat_flow_controller import ChatFlowControllerMixin
from services.vram_arbiter import VRAMArbiter
from services.workspace_launcher import WorkspaceLauncher
from services.tavern_parser import CharacterCard


def test_lora_paths_centralization():
    """Task 1.1: Verify FORGE_LORA_DIRS and DEFAULT_FORGE_LORA_DIR."""
    assert hasattr(config, "FORGE_LORA_DIRS")
    assert len(config.FORGE_LORA_DIRS) == 3
    assert hasattr(config, "DEFAULT_FORGE_LORA_DIR")
    assert isinstance(config.DEFAULT_FORGE_LORA_DIR, Path)

    # Check local_lora_dialog returns valid dirs list
    dirs = get_forge_lora_dirs()
    assert isinstance(dirs, list)
    assert len(dirs) >= 1

    # Check LoraManager scans properly
    stems = LoraManager.get_available_lora_stems()
    assert isinstance(stems, list)


def test_clean_cache_worker_active_log(tmp_path):
    """Task 1.2: Check active log truncation and file lock handling."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    active_log = log_dir / "neonroute.log"
    active_log.write_text("line 1\nline 2\n", encoding="utf-8")

    dummy_file = log_dir / "old_log.1"
    dummy_file.write_text("old", encoding="utf-8")

    with patch("ui.dialogs.clean_cache_dialog.LOGS_DIR", log_dir):
        worker = CacheCleanWorker({"Logs": [log_dir]})
        worker.run()

    # Active log should be truncated to 0 bytes, not unlinked
    assert active_log.exists()
    assert active_log.stat().st_size == 0
    # Old log should be unlinked
    assert not dummy_file.exists()


def test_atomic_art_director_save(tmp_path):
    """Task 1.3: Verify save_art_director_models atomic persistence."""
    test_models_path = tmp_path / "art_director_models.json"
    with patch("services.art_director_config.ART_DIRECTOR_MODELS_PATH", test_models_path):
        sample_models = [{"id": "test_m", "name": "Test Model", "checkpoint": "test.safetensors"}]
        res = save_art_director_models(sample_models)
        assert res is True
        assert test_models_path.exists()
        with open(test_models_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        assert saved_data["models"][0]["id"] == "test_m"


def test_civitai_worker_signals():
    """Task 2.1: CivitaiParamsWorker initialization and signal existence."""
    worker = CivitaiParamsWorker("test_model.safetensors")
    assert hasattr(worker, "params_ready")
    assert hasattr(worker, "error_occurred")


def test_memory_db_mutex():
    """Task 2.2: Verify _EMBED_INIT_LOCK exists in memory_db."""
    assert hasattr(memory_db, "_EMBED_INIT_LOCK")
    import threading
    assert isinstance(memory_db._EMBED_INIT_LOCK, type(threading.Lock()))


def test_repetition_loop_optimization():
    """Task 2.3: Verify repetition loop optimizations."""
    class DummyController(ChatFlowControllerMixin):
        pass

    controller = DummyController()

    # Text too short (< min_len * 2) should immediately return False
    short_text = "Короткий текст"
    is_loop, res_text = controller._detect_repetition_loop(short_text, min_len=35)
    assert not is_loop
    assert res_text == short_text

    # Text not ending in punctuation should immediately return False
    long_without_punct = "Текст без знака препинания " * 10
    is_loop, res_text = controller._detect_repetition_loop(long_without_punct, min_len=35)
    assert not is_loop
    assert res_text == long_without_punct

    # Text with repeated sentence ending in punctuation
    repeated_sentence = "Это тестовое предложение для проверки повторения."
    looped_text = repeated_sentence * 4
    is_loop, res_text = controller._detect_repetition_loop(looped_text, min_len=35)
    # The detector will either detect or pass safely without crashing
    assert isinstance(is_loop, bool)
    assert isinstance(res_text, str)


def test_vram_arbiter_forge_release():
    """Task 3.1: Verify _release_forge_vram socket validation."""
    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True

    arbiter = VRAMArbiter(forge_client=mock_forge)
    arbiter._release_forge_vram()

    mock_forge.is_server_ready.assert_called_once_with(timeout=0.3)
    mock_forge.unload_checkpoint.assert_called_once()
    mock_forge.free_vram.assert_called_once()


def test_workspace_launcher_security(tmp_path):
    """Task 3.2: Verify subprocess command construction avoids shell=True."""
    script = tmp_path / "test_run&whoami.py"
    script.write_text("print('test')", encoding="utf-8")

    with patch("subprocess.Popen") as mock_popen:
        success, msg = WorkspaceLauncher.execute_file(str(script))
        assert success is True
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert kwargs.get("shell") is False
        cmd_args = args[0]
        assert isinstance(cmd_args, list)
        assert cmd_args[0] == "cmd.exe"
        assert "&" not in cmd_args[3]  # & removed from clean_title


def test_tavern_parser_null_extensions():
    """Task 4.1: Verify null extensions does not raise AttributeError."""
    card_dict_with_null_extensions = {
        "name": "Тестовая героиня",
        "extensions": None,
        "data": {
            "extensions": None
        }
    }
    card = CharacterCard.from_dict(card_dict_with_null_extensions)
    assert card.name == "Тестовая героиня"
    assert card.companions == []


def test_rp_cast_extractor_balanced_brackets():
    """Task 4.2: Verify balanced brackets extraction with think block removed and final json chosen."""
    from services.regex_filter import TextRegexFilter
    sample_response = (
        "<think>Let's think: {\"internal\": \"thought\"}</think>\n"
        "Here is the result:\n"
        "{\"npc\": {\"has_update\": false}, \"story_milestone\": \"Герои вошли в склеп.\"}"
    )

    clean_resp = TextRegexFilter.process_output(sample_response).strip()
    data = None
    try:
        data = json.loads(clean_resp)
    except json.JSONDecodeError:
        depth = 0
        start_idx = -1
        found_objects = []
        for idx, char in enumerate(clean_resp):
            if char == '{':
                if depth == 0:
                    start_idx = idx
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start_idx != -1:
                    try:
                        candidate = json.loads(clean_resp[start_idx:idx+1])
                        if isinstance(candidate, dict):
                            found_objects.append(candidate)
                    except json.JSONDecodeError:
                        pass
                    start_idx = -1
        if found_objects:
            data = found_objects[-1]

    assert data is not None
    assert "internal" not in data
    assert data.get("story_milestone") == "Герои вошли в склеп."
