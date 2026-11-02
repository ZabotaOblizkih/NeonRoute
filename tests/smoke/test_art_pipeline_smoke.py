"""
Isolated Smoke Test for Art Generation Pipeline in NeonRoute.
Strict audit diagnostic probe for:
Stage 1: JSON Persistence & Integrity
Stage 2: Reactive UI / Model Synchronization
Stage 3: Prompt & Parameter Assembly before Forge (Mock)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
from services.art_director_config import save_art_director_models, load_art_director_models
from services.art_director.director import ArtDirector
from services.art_director.contracts import VisualIntent, TargetSubject
from services.model_router import ModelRouter
from ui.controllers.hardware_engine import HardwareEngineMixin


class DummyWindow(HardwareEngineMixin):
    """Headless window simulating UI synchronization with HardwareEngineMixin."""

    def __init__(self, art_director, forge_client):
        self.art_director = art_director
        self.forge_client = forge_client
        self.available_forge_checkpoints = []
        self.current_session = None
        self.current_mode = "roleplay"
        self.selected_forge_checkpoint = None

    def _cancel_lms_reload(self):
        pass

    def _reload_active_lms_model_async(self, delay=1.2):
        pass

    def get_effective_mode(self):
        return "roleplay"


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """
    Strict isolation fixture:
    Redirects all model configuration paths to an isolated tmp_path / "art_director_models.json".
    Ensures zero interaction with real data/art_director_models.json.
    """
    tmp_json_path = tmp_path / "art_director_models.json"

    # Reset ModelRouter singleton to ensure clean state
    ModelRouter._instance = None

    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", tmp_json_path)

    import ui.models_manager_dialog as mmd
    if hasattr(mmd, "ART_DIRECTOR_MODELS_PATH"):
        monkeypatch.setattr(mmd, "ART_DIRECTOR_MODELS_PATH", tmp_json_path)

    yield tmp_json_path

    # Teardown: clear singleton
    ModelRouter._instance = None


def test_art_generation_pipeline_smoke(isolated_storage):
    """
    Full end-to-end smoke test validating the generation pipeline across 3 stages.
    """
    tmp_json_path = isolated_storage

    # -------------------------------------------------------------------------
    # Test Entities Definition
    # -------------------------------------------------------------------------
    model_id = "smoke_test_dragon_v1"
    model_name = "Dragon Slayer 1.5"
    checkpoint_file = "dragon_model.safetensors"
    expected_steps = 27
    expected_cfg = 6.5
    expected_ad = True
    expected_negative = "badhand4, zombie"

    dragon_model_profile = {
        "id": model_id,
        "name": model_name,
        "architecture": "sd15",
        "checkpoint": checkpoint_file,
        "routing_description": "Dragon fantasy style with fire and city destruction",
        "active": True,
        "negative_prompt": expected_negative,
        "pipeline": {
            "architecture": "sd15",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": expected_steps,
            "cfg_scale": expected_cfg,
            "clip_skip": 1,
            "enable_hr": False,
            "enable_adetailer": expected_ad,
            "negative_prompt": expected_negative
        }
    }

    # -------------------------------------------------------------------------
    # Stage 1: JSON Persistence & Integrity
    # -------------------------------------------------------------------------
    save_success = save_art_director_models([dragon_model_profile])
    assert save_success is True, "Failed to save model profile via save_art_director_models"
    assert tmp_json_path.exists(), f"Temporary file {tmp_json_path} does not exist on disk"

    # Read raw JSON from disk and verify exact fields
    raw_disk_content = tmp_json_path.read_text(encoding="utf-8-sig")
    disk_data = json.loads(raw_disk_content)

    assert "models" in disk_data, f"Key 'models' missing from {tmp_json_path}"
    saved_models = disk_data["models"]
    assert len(saved_models) == 1, f"Expected 1 model, found {len(saved_models)}"

    saved_entry = saved_models[0]
    assert saved_entry.get("id") == model_id, f"Model ID mismatch: expected {model_id}, got {saved_entry.get('id')}"
    assert saved_entry.get("name") == model_name, f"Model name mismatch: expected {model_name}, got {saved_entry.get('name')}"
    assert saved_entry.get("checkpoint") == checkpoint_file, f"Checkpoint mismatch: expected {checkpoint_file}, got {saved_entry.get('checkpoint')}"
    assert saved_entry.get("active") is True, "Model 'active' flag is not True"

    pipe = saved_entry.get("pipeline", {})
    assert pipe.get("steps") == expected_steps, f"Steps mismatch in JSON: expected {expected_steps}, got {pipe.get('steps')}"
    assert pipe.get("cfg_scale") == expected_cfg, f"CFG mismatch in JSON: expected {expected_cfg}, got {pipe.get('cfg_scale')}"
    assert pipe.get("negative_prompt") == expected_negative, f"Negative prompt mismatch in JSON: expected '{expected_negative}', got '{pipe.get('negative_prompt')}'"
    assert pipe.get("enable_adetailer") is True, f"enable_adetailer mismatch in JSON: expected True, got {pipe.get('enable_adetailer')}"

    # -------------------------------------------------------------------------
    # Stage 2: Reactive UI Synchronization
    # -------------------------------------------------------------------------
    art_director = ArtDirector()
    mock_forge = MagicMock()
    mock_forge.get_models.return_value = [{"title": checkpoint_file, "model_name": "dragon_model"}]

    window = DummyWindow(art_director=art_director, forge_client=mock_forge)

    # Initial state: checkpoint not yet loaded in window
    assert model_name not in window.available_forge_checkpoints

    # Trigger soft reload of models (models_updated / reload_models)
    art_director.reload_models()
    window._on_forge_models_discovered([])

    # Verify model is available in checkpoints without application restart
    assert model_name in window.available_forge_checkpoints, (
        f"Reactive sync failed: '{model_name}' not found in available checkpoints: "
        f"{window.available_forge_checkpoints}"
    )

    # Verify model presence in ArtDirector router profiles
    router_profile_names = [p.get("name") for p in art_director.router.profiles]
    assert model_name in router_profile_names, (
        f"ArtDirector router out of sync: '{model_name}' not in {router_profile_names}"
    )

    # -------------------------------------------------------------------------
    # Stage 3: Prompt & Parameter Assembly before Forge (Mock)
    # -------------------------------------------------------------------------
    user_prompt = "dragon, fire, city, fantasy"
    user_scene_negative = "no people"
    elaria_dna_hint = "anime-sianime"
    target_model_key = model_id

    intent = VisualIntent(
        prompt=user_prompt,
        user_neg=user_scene_negative,
        companion_dna=elaria_dna_hint,
        suggested_model=target_model_key,
        target_subject=TargetSubject.COMPANION,
        mode="roleplay"
    )

    plan = art_director.plan_from_visual_intent(
        visual_intent=intent,
        preferred_style=target_model_key,
        companion_dna=elaria_dna_hint,
        mode="roleplay"
    )

    forge_kwargs = plan.to_forge_kwargs()
    mock_forge.text_to_image(**forge_kwargs)

    assert mock_forge.text_to_image.called, "mock_forge.text_to_image was not called"
    call_kwargs = mock_forge.text_to_image.call_args.kwargs

    # Diagnostic formatting helper
    dump_context = f"\n=== FORGE KWARGS DUMP ===\n{json.dumps({k: str(v) for k, v in call_kwargs.items()}, indent=2)}\n"

    # 1. Positive Prompt checks
    actual_prompt = call_kwargs.get("prompt", "")
    assert user_prompt in actual_prompt, (
        f"Base prompt '{user_prompt}' missing from kwargs['prompt']!\n"
        f"Actual prompt: '{actual_prompt}'{dump_context}"
    )
    assert elaria_dna_hint in actual_prompt, (
        f"Elaria DNA hint '{elaria_dna_hint}' missing from kwargs['prompt']!\n"
        f"Actual prompt: '{actual_prompt}'{dump_context}"
    )

    # 2. Negative Prompt checks
    actual_negative = call_kwargs.get("negative_prompt", "")
    assert "badhand4" in actual_negative, (
        f"Model negative token 'badhand4' missing from kwargs['negative_prompt']!\n"
        f"Actual negative: '{actual_negative}'{dump_context}"
    )
    assert "zombie" in actual_negative, (
        f"Model negative token 'zombie' missing from kwargs['negative_prompt']!\n"
        f"Actual negative: '{actual_negative}'{dump_context}"
    )
    assert "no people" in actual_negative, (
        f"User scene negative 'no people' missing from kwargs['negative_prompt']!\n"
        f"Actual negative: '{actual_negative}'{dump_context}"
    )

    # 3. Pipeline inference parameters
    assert call_kwargs.get("steps") == expected_steps, (
        f"Steps mismatch: expected {expected_steps}, got {call_kwargs.get('steps')}{dump_context}"
    )
    assert call_kwargs.get("cfg_scale") == expected_cfg, (
        f"CFG scale mismatch: expected {expected_cfg}, got {call_kwargs.get('cfg_scale')}{dump_context}"
    )
    assert call_kwargs.get("enable_adetailer") is True, (
        f"enable_adetailer is not True: got {call_kwargs.get('enable_adetailer')}{dump_context}"
    )
