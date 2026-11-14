"""
Isolated Smoke Test: ADetailer Prompt & Base Negative Passthrough + UI Cleanup.
Validates end-to-end flow:
  ArtDirector -> GenerationPlan.to_forge_kwargs() -> ForgeClient.text_to_image() (mocked HTTP)
and verifies ADetailerSettingsDialog has no prompt/negative input widgets.
"""

import base64
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image
from PyQt6.QtWidgets import QLabel
from qfluentwidgets import LineEdit

from services.art_director.contracts import VisualIntent
from services.art_director.director import ArtDirector
from services.art_director.prompting import parse_image_prompt_flags
from services.art_director_config import save_art_director_models
from services.forge_client import ForgeClient
from services.model_router import ModelRouter
from ui.models_manager.adetailer_dialog import ADetailerSettingsDialog


def _make_png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(16, 24, 32)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@pytest.fixture
def isolated_passthrough_env(tmp_path, monkeypatch, qapp):
    models_json = tmp_path / "data" / "art_director_models.json"
    models_json.parent.mkdir(parents=True, exist_ok=True)
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    ModelRouter._instance = None
    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", models_json)
    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", models_json)
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", models_json)

    yield tmp_path, models_json, outputs_dir

    inst = getattr(ModelRouter, "_instance", None)
    if inst and hasattr(inst, "cancel_deferred_warmup"):
        inst.cancel_deferred_warmup()
    ModelRouter._instance = None


def test_adetailer_prompt_and_base_negative_passthrough_smoke(isolated_passthrough_env, monkeypatch):
    _tmp_path, _models_json, outputs_dir = isolated_passthrough_env

    model_id = "smoke_juggernaut_light"
    ckpt_name = "Juggernaut_XI_Light_byRunDiffusion.safetensors"
    custom_base_neg = "custom_base_negative_from_models_manager, bad anatomy, plastic skin"

    profile = {
        "id": model_id,
        "name": "Juggernaut XI Light Smoke",
        "architecture": "sdxl",
        "checkpoint": ckpt_name,
        "routing_description": "Photorealistic cinematic portrait",
        "negative_prompt": custom_base_neg,
        "active": True,
        "pipeline": {
            "architecture": "sdxl",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": 6,
            "cfg_scale": 1.7,
            "clip_skip": 1,
            "enable_hr": False,
            "enable_adetailer": True,
            "adetailer_face_model": "face_yolov8m.pt",
            "adetailer_face_confidence": 0.44,
            "adetailer_face_denoise": 0.21,
            "adetailer_face_inpaint_size": 768,
            "adetailer_hands_enabled": True,
            "adetailer_hands_model": "hand_yolov9c.pt",
            "adetailer_hands_confidence": 0.40,
            "adetailer_hands_denoise": 0.26,
            "adetailer_hands_inpaint_size": 512,
            "adetailer_hands_mask_blur": 8,
            "adetailer_hands_prompt": "detailed hand, realistic fingers, legacy override",
            "adetailer_hands_negative_prompt": "woman, girl, body, legacy neg",
            "negative_prompt": custom_base_neg,
        },
    }
    assert save_art_director_models([profile]) is True

    director = ArtDirector()
    director.reload_models()

    raw_tag = (
        f"beautiful woman, sitting by window, natural lighting, thigh-up portrait, medium shot "
        f"--ar portrait --model {model_id}"
    )
    parsed = parse_image_prompt_flags(raw_tag)
    intent = VisualIntent(
        prompt=parsed.prompt,
        user_neg=parsed.negative,
        suggested_model=parsed.model_style or model_id,
        aspect_ratio=parsed.aspect_ratio,
        mode="assistant",
    )
    plan = director.plan_from_visual_intent(
        visual_intent=intent,
        preferred_style=model_id,
        available_models=[{"title": ckpt_name, "model_name": "Juggernaut_XI_Light_byRunDiffusion"}],
        mode="assistant",
    )

    # Mock ForgeClient HTTP layer to capture exact JSON payload sent to /sdapi/v1/txt2img
    client = ForgeClient(base_url="http://127.0.0.1:7860")
    monkeypatch.setattr(client, "is_server_ready", lambda timeout=1.0: True)
    monkeypatch.setattr(client, "set_model", lambda ckpt: True)
    monkeypatch.setattr(client, "get_models", lambda: [{"title": ckpt_name, "model_name": "Juggernaut_XI_Light_byRunDiffusion"}])
    monkeypatch.setattr(client, "free_vram", lambda *a, **kw: True)
    monkeypatch.setattr(client, "has_script", lambda *a, **kw: False)

    captured_payloads = []
    valid_b64 = _make_png_b64()

    def _fake_post(url, json=None, timeout=None, **kwargs):
        if str(url).endswith("/sdapi/v1/txt2img"):
            captured_payloads.append(json)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"images": [valid_b64], "info": "{}"}
            return resp
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        return resp

    def _fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"progress": 1.0, "state": {}}
        return resp

    class _MockSession:
        def post(self, url, json=None, timeout=None, **kwargs):
            return _fake_post(url, json=json, timeout=timeout, **kwargs)

        def close(self):
            pass

    monkeypatch.setattr("services.forge_client.requests.post", _fake_post)
    monkeypatch.setattr("services.forge_client.requests.get", _fake_get)
    monkeypatch.setattr("services.forge_client.requests.Session", lambda: _MockSession())

    forge_kwargs = plan.to_forge_kwargs(output_dir=outputs_dir)
    out_path = client.text_to_image(**forge_kwargs)
    assert out_path.exists()
    assert len(captured_payloads) == 1

    payload = captured_payloads[0]
    ad_args = payload["alwayson_scripts"]["ADetailer"]["args"]
    assert ad_args[0] is True
    assert ad_args[1] is False
    assert len(ad_args) >= 4

    face_slot = ad_args[2]
    hand_slot = ad_args[3]

    # Strict passthrough checks for both Face and Hands slots
    assert face_slot["ad_prompt"] == payload["prompt"]
    assert hand_slot["ad_prompt"] == payload["prompt"]
    assert face_slot["ad_negative_prompt"] == payload["negative_prompt"]
    assert hand_slot["ad_negative_prompt"] == payload["negative_prompt"]
    assert custom_base_neg in payload["negative_prompt"]

    # Ensure legacy hardcoded defaults / overrides never leak into slots
    for slot in (face_slot, hand_slot):
        assert "woman, girl, body" not in slot["ad_negative_prompt"]
        assert "detailed hand, realistic fingers" not in slot["ad_prompt"]
        assert slot["ad_use_steps"] is False
        assert slot["ad_use_inpaint_width_height"] is True
        assert slot["ad_use_cfg_scale"] is False


def test_adetailer_dialog_has_no_prompt_or_negative_inputs(isolated_passthrough_env):
    dialog = ADetailerSettingsDialog(
        {
            "enable_adetailer": True,
            "adetailer_hands_enabled": True,
            "adetailer_hands_prompt": "legacy prompt",
            "adetailer_hands_negative_prompt": "legacy negative",
        },
        is_xl=True,
    )
    assert not hasattr(dialog, "edit_hands_prompt")
    assert not hasattr(dialog, "edit_hands_neg")
    assert not hasattr(dialog, "btn_reset_hands")
    assert len(dialog.findChildren(LineEdit)) == 0
    assert all(
        "промпт" not in lbl.text().lower() and "негатив" not in lbl.text().lower()
        for lbl in dialog.findChildren(QLabel)
    )

    settings = dialog.get_settings()
    assert settings["adetailer_hands_prompt"] == ""
    assert settings["adetailer_hands_negative_prompt"] == ""
    dialog.reject()
