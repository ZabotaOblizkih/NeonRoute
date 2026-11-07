"""
Isolated Smoke Test for Roleplay Art Generation Pipeline in NeonRoute.
End-to-end verification of RP scene art generation across two architectural branches:
1. SD 1.5 Auto Mode (Semantic routing -> CyberRealistic SD15 -> Danbooru tags, 512x768)
2. SDXL Manual Mode (Direct checkpoint -> CounterfeitXL SDXL -> Natural prose, 896x1152)

Invariants verified:
- Pure isolation via tmp_path and mocks (no live servers or real data/ writes)
- Formatting directives (SD 1.5 vs SDXL) and absence of '1girl/1boy' slash placeholders
- Accurate scene context assembly (location, inventory, passive vs active gear)
- Location deduplication stub in ArtDirector
- Strict dispatch of generation kwargs to ForgeClient.text_to_image
"""

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from services.art_director_config import save_art_director_models
from services.art_director.director import ArtDirector
from services.model_router import ModelRouter, normalize_model_choice
from services.tavern_parser import CharacterCard
from services.roleplay_flow_controller import RoleplayFlowControllerMixin
from ui.controllers.hardware_engine import HardwareEngineMixin


class DummyRPWindow(RoleplayFlowControllerMixin, HardwareEngineMixin):
    """Headless window simulating UI roleplay synchronization with HardwareEngineMixin."""

    def __init__(self, art_director, forge_client, lm_client, session_id="test_rp_session"):
        self.art_director = art_director
        self.forge_client = forge_client
        self.lm_client = lm_client
        self.current_mode = "roleplay"
        self.selected_forge_checkpoint = None
        self.current_session = {"id": session_id}
        self.active_character = CharacterCard(
            name="Серафина",
            gender="female",
            visual_dna="amber eyes, long blonde hair"
        )
        self.available_forge_checkpoints = []
        self._active_tasks = {}

    def get_effective_mode(self):
        return "roleplay"

    def _cancel_lms_reload(self):
        pass

    def _reload_active_lms_model_async(self, delay=1.2):
        pass

    def _is_forge_available(self):
        return True


@pytest.fixture
def isolated_smoke_env(tmp_path, monkeypatch):
    """
    Strict isolation fixture:
    Redirects model registry to isolated tmp_path.
    Configures two synthetic reference models (SD 1.5 default and SDXL manual).
    """
    tmp_json_path = tmp_path / "art_director_models.json"
    ModelRouter._instance = None

    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", tmp_json_path)

    import ui.models_manager_dialog as mmd
    if hasattr(mmd, "ART_DIRECTOR_MODELS_PATH"):
        monkeypatch.setattr(mmd, "ART_DIRECTOR_MODELS_PATH", tmp_json_path)

    model_sd15 = {
        "id": "model_sd15_classic",
        "name": "CyberRealistic SD15",
        "architecture": "sd15",
        "checkpoint": "cyberrealistic_v50.safetensors",
        "is_default": True,
        "style_triggers": ["realistic", "photo", "dialogue"],
        "routing_description": "photorealistic portrait, natural light, dialogue",
        "active": True,
        "pipeline": {
            "architecture": "sd15",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": 25,
            "cfg_scale": 6.0,
            "clip_skip": 1,
            "enable_hr": False,
            "enable_adetailer": True,
            "negative_prompt": "bad hands, blurry, lowres"
        }
    }

    model_sdxl = {
        "id": "model_sdxl_anime",
        "name": "CounterfeitXL SDXL",
        "architecture": "sdxl",
        "checkpoint": "counterfeitxl_v25.safetensors",
        "is_default": False,
        "style_triggers": ["anime", "action", "fantasy"],
        "routing_description": "vibrant anime action, dynamic lighting",
        "active": True,
        "pipeline": {
            "architecture": "sdxl",
            "sampler": "DPM++ 2M SDE Karras",
            "scheduler": "Karras",
            "steps": 28,
            "cfg_scale": 6.5,
            "clip_skip": 2,
            "enable_hr": False,
            "enable_adetailer": False,
            "negative_prompt": "flat color, bad anatomy, deformed"
        }
    }

    assert save_art_director_models([model_sd15, model_sdxl]) is True
    art_director = ArtDirector()
    art_director.reload_models()

    # Isolate session images directory and VRAM arbiter
    monkeypatch.setattr("services.session_manager.SessionManager.get_session_images_dir", lambda sid: tmp_path / "images")
    monkeypatch.setattr("services.vram_arbiter.VRAMArbiter.release_llm_for_diffusion", MagicMock())

    yield art_director

    ModelRouter._instance = None


def test_rp_art_pipeline_sd15_auto(isolated_smoke_env, monkeypatch):
    """
    Scenario 1: SD 1.5 Auto Mode
    - Scene Archetype: dialogue
    - UI Model Selection: '✨ Арт-директор (Авто)' -> routes to default SD 1.5 model
    - System directive: SD 1.5 Danbooru rules, no '1girl/1boy'
    - Context: sheathed weapons, outfit
    - Plan: 512x768 resolution, SD 1.5 pipeline parameters
    - Forge: text_to_image receives expected kwargs
    """
    art_director = isolated_smoke_env

    # 1. Mock RPCastManager cast state
    cast_data = {
        "scene_type": "dialogue",
        "environment": {
            "location_dna": "enchanted forest, ancient trees",
            "scene_ambience": "quiet morning, sunbeams"
        },
        "companion_state": {
            "visual_dna": "amber eyes, long blonde hair",
            "current_outfit": "canonical traveler tunic"
        },
        "inventory": ["кинжал", "травяной чай"]
    }
    monkeypatch.setattr("services.rp_cast_manager.RPCastManager.load_cast", lambda *args, **kwargs: cast_data)

    # 2. Mock LM and Forge clients
    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = (
        "[IMAGE_PROMPT: 1girl, solo, amber eyes, long blonde hair, canonical traveler tunic, "
        "sitting at wooden table, holding mug, enchanted forest, morning light --ar portrait --model model_sd15_classic]"
    )

    mock_forge = MagicMock()
    mock_forge.get_models.return_value = [
        {"title": "cyberrealistic_v50.safetensors", "model_name": "cyberrealistic_v50"},
        {"title": "counterfeitxl_v25.safetensors", "model_name": "counterfeitxl_v25"}
    ]
    mock_forge.text_to_image.return_value = "C:/tmp/images/output_sd15.png"
    mock_forge.is_server_ready.return_value = True
    monkeypatch.setattr("services.forge_client.ForgeClient.get_instance", lambda: mock_forge)

    # 3. Initialize Headless Window in Auto Mode
    window = DummyRPWindow(art_director=art_director, forge_client=mock_forge, lm_client=mock_lm)
    window.selected_forge_checkpoint = "✨ Арт-директор (Авто)"
    active_pref_style = normalize_model_choice(window.selected_forge_checkpoint)
    assert active_pref_style is None, "Auto mode must normalize to None"

    # 4. Trigger scene art pipeline
    scene_text = "Серафина сделала глоток чая и улыбнулась утреннему солнцу."
    res = window._scene_art_pipeline(scene_text=scene_text, ckpt=active_pref_style)

    # 5. Assert LM Chat Completion call integrity
    assert mock_lm.chat_completion.call_count == 1
    llm_kwargs = mock_lm.chat_completion.call_args.kwargs
    messages = llm_kwargs["messages"]
    system_content = messages[0]["content"]
    user_content = messages[1]["content"]

    # Directive checks
    assert "CRITICAL FORMAT RULES (SD 1.5):" in system_content
    assert "Danbooru/SD positive tags" in system_content
    assert "1girl/1boy" not in system_content

    # Assert presence of negative tag in text model prompt
    full_llm_prompt = " ".join(m.get("content", "") for m in messages)
    assert "--neg" in full_llm_prompt, "Expected tag '--neg' to be present in SD 1.5 text model prompt"

    # Scene context payload checks
    assert "Generate concise, comma-separated Danbooru tags for this RP scene:" in user_content
    assert "Location: enchanted forest, ancient trees (quiet morning, sunbeams)" in user_content
    assert "Outfit: canonical traveler tunic" in user_content
    assert "- Equipment:" not in user_content  # In dialogue scene, passive weapons are not injected as active equipment
    assert "CRITICAL: Output ONLY comma-separated tags inside [IMAGE_PROMPT: ...]" in user_content

    # 6. Assert GenerationPlan resolution
    plan = res["plan"]
    assert plan is not None
    assert plan.target_checkpoint == "cyberrealistic_v50.safetensors"
    assert plan.width == 512
    assert plan.height == 768
    assert plan.steps == 25
    assert plan.cfg_scale == 6.0
    assert plan.sampler_name == "DPM++ 2M"
    assert plan.scheduler == "Karras"

    # 7. Assert Forge text_to_image invocation
    assert mock_forge.text_to_image.call_count == 1
    forge_kwargs = mock_forge.text_to_image.call_args.kwargs
    assert forge_kwargs["checkpoint"] == "cyberrealistic_v50.safetensors"
    assert forge_kwargs["width"] == 512
    assert forge_kwargs["height"] == 768
    assert forge_kwargs["steps"] == 25
    assert forge_kwargs["cfg_scale"] == 6.0
    assert "1girl, solo" in forge_kwargs["prompt"]
    assert "amber eyes" in forge_kwargs["prompt"]
    assert "bad hands, blurry" in forge_kwargs["negative_prompt"]


def test_rp_art_pipeline_sdxl_manual(isolated_smoke_env, monkeypatch):
    """
    Scenario 2: SDXL Manual Mode
    - Scene Archetype: action
    - UI Model Selection: 'counterfeitxl_v25.safetensors' (Manual SDXL)
    - System directive: SDXL prose rules, no '1girl/1boy', no tag spam
    - Context: active equipment (runic sword, steel shield)
    - Plan: 896x1152 resolution, SDXL pipeline parameters, location deduplication stub
    - Forge: text_to_image receives expected kwargs
    """
    art_director = isolated_smoke_env

    # 1. Mock RPCastManager cast state
    cast_data = {
        "scene_type": "action",
        "environment": {
            "location_dna": "ancient ruins, mossy stone walls",
            "scene_ambience": "smoke and flying sparks"
        },
        "companion_state": {
            "visual_dna": "amber eyes, long blonde hair",
            "current_outfit": "combat armor"
        },
        "inventory": ["рунный меч", "стальной щит"]
    }
    monkeypatch.setattr("services.rp_cast_manager.RPCastManager.load_cast", lambda *args, **kwargs: cast_data)

    # 2. Mock LM and Forge clients
    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = (
        "[IMAGE_PROMPT: dynamic waist-up action shot of a fierce warrior woman in combat armor, "
        "intense battle-focused gaze, swinging a glowing rune sword, sparks flying in smoky ancient ruins, "
        "dramatic rim lighting --ar portrait --model model_sdxl_anime]"
    )

    mock_forge = MagicMock()
    mock_forge.get_models.return_value = [
        {"title": "cyberrealistic_v50.safetensors", "model_name": "cyberrealistic_v50"},
        {"title": "counterfeitxl_v25.safetensors", "model_name": "counterfeitxl_v25"}
    ]
    mock_forge.text_to_image.return_value = "C:/tmp/images/output_sdxl.png"
    mock_forge.is_server_ready.return_value = True
    monkeypatch.setattr("services.forge_client.ForgeClient.get_instance", lambda: mock_forge)

    # 3. Initialize Headless Window in Manual SDXL Mode
    window = DummyRPWindow(art_director=art_director, forge_client=mock_forge, lm_client=mock_lm)
    window.selected_forge_checkpoint = "counterfeitxl_v25.safetensors"
    active_pref_style = normalize_model_choice(window.selected_forge_checkpoint)
    assert active_pref_style == "counterfeitxl_v25.safetensors"

    # 4. Trigger scene art pipeline
    scene_text = "Серафина ринулась вперед, взмахнув рунным мечом против надвигающейся тьмы!"
    res = window._scene_art_pipeline(scene_text=scene_text, ckpt=active_pref_style)

    # 5. Assert LM Chat Completion call integrity
    assert mock_lm.chat_completion.call_count == 1
    llm_kwargs = mock_lm.chat_completion.call_args.kwargs
    messages = llm_kwargs["messages"]
    system_content = messages[0]["content"]
    user_content = messages[1]["content"]

    # Directive checks
    assert "CRITICAL FORMAT RULES (SDXL):" in system_content
    assert "Do NOT use Danbooru tag spam" in system_content
    assert "NEVER use obsolete quality tags" in system_content
    assert "1girl/1boy" not in system_content

    # Assert presence of negative tag in text model prompt
    full_llm_prompt = " ".join(m.get("content", "") for m in messages)
    assert "--neg" in full_llm_prompt, "Expected tag '--neg' to be present in SDXL text model prompt"

    # Scene context payload checks
    assert "Generate a concise descriptive visual prompt (2-3 sentences) for this RP scene:" in user_content
    assert "Location: ancient ruins, mossy stone walls (smoke and flying sparks)" in user_content
    assert "Outfit: combat armor" in user_content
    assert "- Equipment: рунный меч, стальной щит" in user_content
    assert "CRITICAL: Output ONLY the descriptive prompt inside [IMAGE_PROMPT: ...]" in user_content
    assert "Do NOT write prose or paragraphs" not in user_content

    # 6. Assert GenerationPlan resolution
    plan = res["plan"]
    assert plan is not None
    assert plan.target_checkpoint == "counterfeitxl_v25.safetensors"
    assert plan.width == 896
    assert plan.height == 1152
    assert plan.steps == 28
    assert plan.cfg_scale == 6.5
    assert plan.sampler_name == "DPM++ 2M SDE"
    assert plan.scheduler == "Karras"

    # Location deduplication stub check: location_dna must NOT be forcefully appended to positive_prompt
    assert not plan.positive_prompt.endswith("ancient ruins, mossy stone walls")

    # 7. Assert Forge text_to_image invocation
    assert mock_forge.text_to_image.call_count == 1
    forge_kwargs = mock_forge.text_to_image.call_args.kwargs
    assert forge_kwargs["checkpoint"] == "counterfeitxl_v25.safetensors"
    assert forge_kwargs["width"] == 896
    assert forge_kwargs["height"] == 1152
    assert forge_kwargs["steps"] == 28
    assert forge_kwargs["cfg_scale"] == 6.5
    assert "dynamic waist-up action shot" in forge_kwargs["prompt"]
    assert "1girl, solo" not in forge_kwargs["prompt"]
    assert "flat color, bad anatomy" in forge_kwargs["negative_prompt"]
