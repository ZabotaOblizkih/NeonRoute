"""
Unit and integration tests for ADetailerSettingsDialog and pipeline configuration.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication, QDialog

from ui.models_manager.adetailer_dialog import ADetailerSettingsDialog
from ui.models_manager_dialog import ModelsManagerDialog
from services.art_director.director import ArtDirector
from services.art_director.plan import GenerationPlan
from services.art_director_config import save_art_director_models, load_art_director_models


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    tmp_json_path = tmp_path / "art_director_models.json"
    from services.model_router import ModelRouter
    ModelRouter._instance = None
    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", tmp_json_path)
    return tmp_json_path


def test_adetailer_dialog_initialization_and_save(qapp):
    initial_params = {
        "enable_adetailer": True,
        "adetailer_face_model": "face_yolov8m.pt",
        "adetailer_face_confidence": 0.44,
        "adetailer_face_denoise": 0.32,
        "adetailer_face_inpaint_size": 768,
        "adetailer_hands_enabled": True,
        "adetailer_hands_model": "hand_yolov9c.pt",
        "adetailer_hands_confidence": 0.38,
        "adetailer_hands_denoise": 0.40,
        "adetailer_hands_inpaint_size": 512,
        "adetailer_hands_mask_blur": 8
    }

    dialog = ADetailerSettingsDialog(initial_params, is_xl=True)
    assert dialog.switch_master.isChecked() is True
    assert dialog.switch_hands.isChecked() is True
    assert dialog.combo_face_model.currentText() == "face_yolov8m.pt"
    assert dialog.combo_hands_model.currentText() == "hand_yolov9c.pt"
    assert abs(dialog.spin_face_conf.value() - 0.44) < 1e-3
    assert abs(dialog.spin_hands_conf.value() - 0.38) < 1e-3

    # Modify parameters
    dialog.spin_face_conf.setValue(0.50)
    dialog.spin_hands_denoise.setValue(0.42)
    dialog._on_save_clicked()

    assert dialog.result_params["adetailer_face_confidence"] == 0.50
    assert dialog.result_params["adetailer_hands_denoise"] == 0.42
    assert dialog.result_params["adetailer_hands_mask_blur"] == 8
    assert dialog.result_params["enable_adetailer"] is True
    assert dialog.result_params["adetailer_face_inpaint_size"] == 768
    assert dialog.result_params["adetailer_inpaint_size"] == 768


def test_adetailer_dialog_legacy_key_fallback(qapp):
    params = {"adetailer_inpaint_size": 512}
    dialog = ADetailerSettingsDialog(params, is_xl=True)
    assert dialog.spin_face_inpaint.value() == 512


def test_adetailer_dialog_master_off(qapp):
    initial_params = {
        "enable_adetailer": False,
        "adetailer_face_model": "face_yolov8n.pt",
    }
    dialog = ADetailerSettingsDialog(initial_params, is_xl=False)
    assert dialog.switch_master.isChecked() is False
    assert dialog.card_face.isEnabled() is False
    assert dialog.card_hands.isEnabled() is False


def test_models_manager_dialog_badge_update(qapp, tmp_path, monkeypatch):
    tmp_json = tmp_path / "art_director_models.json"
    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", tmp_json)
    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", tmp_json)
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", tmp_json)

    dummy_models = [{
        "id": "test_m",
        "name": "Test Model",
        "checkpoint": "test.safetensors",
        "active": True,
        "pipeline": {
            "enable_adetailer": True,
            "adetailer_face_model": "face_yolov8m.pt",
            "adetailer_hands_enabled": True,
            "adetailer_hands_model": "hand_yolov9c.pt"
        }
    }]
    save_art_director_models(dummy_models)

    manager = ModelsManagerDialog()
    assert hasattr(manager, "btn_adetailer_config")
    assert hasattr(manager, "lbl_ad_status")

    # Verify badge text
    assert "ВКЛ" in manager.lbl_ad_status.text()
    assert "v8m" in manager.lbl_ad_status.text()
    assert "v9c" in manager.lbl_ad_status.text()

    # Update to OFF
    manager._update_ad_status_label({"enable_adetailer": False})
    assert manager.lbl_ad_status.text() == "ВЫКЛ"
    manager.reject()


def test_art_director_plan_uses_pipeline_adetailer(isolated_storage):
    ad = ArtDirector()
    profiles = load_art_director_models()
    assert len(profiles) > 0
    target_model_key = profiles[0]["id"]

    pipe_override = {
        "enable_adetailer": True,
        "adetailer_face_model": "face_yolov8m.pt",
        "adetailer_face_confidence": 0.44,
        "adetailer_face_denoise": 0.32,
        "adetailer_face_inpaint_size": 768,
        "adetailer_hands_enabled": True,
        "adetailer_hands_model": "hand_yolov9c.pt",
        "adetailer_hands_confidence": 0.38,
        "adetailer_hands_denoise": 0.40,
        "adetailer_hands_inpaint_size": 512,
        "adetailer_hands_mask_blur": 8
    }

    # Save to model profile
    profiles[0]["pipeline"] = pipe_override
    save_art_director_models(profiles)
    ad.reload_models()

    plan = ad.plan_generation(
        raw_prompt="cinematic portrait of a cyberpunk hacker with detailed hands",
        preferred_style=target_model_key,
        mode="assistant"
    )

    assert plan.enable_adetailer is True
    assert plan.adetailer_face_model == "face_yolov8m.pt"
    assert abs(plan.adetailer_face_confidence - 0.44) < 1e-3
    assert plan.adetailer_inpaint_size == 768
    assert plan.adetailer_hands_enabled is True
    assert plan.adetailer_hands_model == "hand_yolov9c.pt"
    assert abs(plan.adetailer_hands_confidence - 0.38) < 1e-3
    assert plan.adetailer_hands_inpaint_size == 512

    # Check forge kwargs adaptive steps and slot payload
    kwargs = plan.to_forge_kwargs()
    alwayson = kwargs.get("alwayson_scripts", {})
    assert "ADetailer" in alwayson
    ad_args = alwayson["ADetailer"]["args"]
    assert ad_args[0] is True  # Master enable
    assert ad_args[1] is False  # skip img2img

    face_slot = ad_args[2]
    assert face_slot["ad_model"] == "face_yolov8m.pt"
    assert face_slot["ad_confidence"] == 0.44
    assert face_slot["ad_inpaint_width"] == 768
    assert face_slot["ad_steps"] >= 4

    hand_slot = ad_args[3]
    assert hand_slot["ad_model"] == "hand_yolov9c.pt"
    assert hand_slot["ad_confidence"] == 0.38
    assert hand_slot["ad_inpaint_width"] == 512
    assert hand_slot["ad_mask_blur"] == 8
    assert hand_slot["ad_steps"] >= 4


def test_adetailer_hands_prompts_dialog_and_pipeline(qapp, isolated_storage):
    from ui.models_manager.adetailer_dialog import DEFAULT_HANDS_PROMPT, DEFAULT_HANDS_NEGATIVE

    initial_params = {
        "enable_adetailer": True,
        "adetailer_hands_enabled": True,
        "adetailer_hands_prompt": "custom realistic hands",
        "adetailer_hands_negative_prompt": "custom bad hands"
    }

    dialog = ADetailerSettingsDialog(initial_params)
    assert dialog.edit_hands_prompt.text() == "custom realistic hands"
    assert dialog.edit_hands_neg.text() == "custom bad hands"

    # Test reset button
    dialog.btn_reset_hands.click()
    assert dialog.edit_hands_prompt.text() == DEFAULT_HANDS_PROMPT
    assert dialog.edit_hands_neg.text() == DEFAULT_HANDS_NEGATIVE

    # Test setting custom and saving
    dialog.edit_hands_prompt.setText("masterpiece fingers")
    dialog.edit_hands_neg.setText("missing fingers, extra digits")
    dialog._on_save_clicked()
    assert dialog.result_params["adetailer_hands_prompt"] == "masterpiece fingers"
    assert dialog.result_params["adetailer_hands_negative_prompt"] == "missing fingers, extra digits"

    # Integration with ArtDirector
    ad = ArtDirector()
    profiles = load_art_director_models()
    assert len(profiles) > 0
    profiles[0]["pipeline"].update(dialog.result_params)
    save_art_director_models(profiles)
    ad.reload_models()

    plan = ad.plan_generation(
        raw_prompt="girl drawing with hands",
        preferred_style=profiles[0]["id"],
        mode="assistant"
    )
    assert plan.adetailer_hands_prompt == "masterpiece fingers"
    assert plan.adetailer_hands_negative_prompt == "missing fingers, extra digits"

    forge_kwargs = plan.to_forge_kwargs()
    hand_slot = forge_kwargs["alwayson_scripts"]["ADetailer"]["args"][3]
    assert hand_slot["ad_prompt"] == "masterpiece fingers"
    assert hand_slot["ad_negative_prompt"] == "missing fingers, extra digits"

    # Test [SEP] is stripped if present in config
    plan.adetailer_hands_prompt = "[SEP] clean isolated hands"
    kwargs_stripped = plan.to_forge_kwargs()
    slot_stripped = kwargs_stripped["alwayson_scripts"]["ADetailer"]["args"][3]
    assert slot_stripped["ad_prompt"] == "clean isolated hands"

    # Test default hand prompt is clean without [SEP]
    plan.adetailer_hands_prompt = None
    kwargs_default = plan.to_forge_kwargs()
    slot_default = kwargs_default["alwayson_scripts"]["ADetailer"]["args"][3]
    assert slot_default["ad_prompt"] == "detailed hand, realistic fingers, anatomically correct, sharp focus"

