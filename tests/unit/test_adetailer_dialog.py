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
    manager.close()
    qapp.processEvents()


def test_art_director_plan_uses_pipeline_adetailer(isolated_storage, qapp):
    qapp.processEvents()
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
    assert face_slot["ad_use_inpaint_width_height"] is True
    assert face_slot["ad_use_steps"] is True
    assert face_slot["ad_use_cfg_scale"] is False
    assert face_slot["ad_prompt"] == plan.prompt
    assert face_slot["ad_negative_prompt"] == plan.negative_prompt

    hand_slot = ad_args[3]
    assert hand_slot["ad_model"] == "hand_yolov9c.pt"
    assert hand_slot["ad_confidence"] == 0.38
    assert hand_slot["ad_inpaint_width"] == 512
    assert hand_slot["ad_use_inpaint_width_height"] is True
    assert hand_slot["ad_use_steps"] is True
    assert hand_slot["ad_use_cfg_scale"] is False
    assert hand_slot["ad_mask_blur"] == 8
    assert hand_slot["ad_prompt"] == plan.prompt
    assert hand_slot["ad_negative_prompt"] == plan.negative_prompt


def test_adetailer_lightning_steps_and_transparent_params():
    plan = GenerationPlan(
        positive_prompt="beautiful woman, sitting by window, thigh-up portrait, medium shot",
        negative_prompt="CGI, Unreal, bad anatomy",
        steps=6,
        cfg_scale=1.7,
        enable_adetailer=True,
        adetailer_face_model="face_yolov8m.pt",
        adetailer_face_denoise=0.21,
        adetailer_face_inpaint_size=768,
        adetailer_hands_enabled=True,
        adetailer_hands_model="hand_yolov9c.pt",
        adetailer_hands_denoise=0.26,
        adetailer_hands_inpaint_size=512,
        adetailer_hands_mask_blur=8,
    )
    kwargs = plan.to_forge_kwargs()
    ad_args = kwargs["alwayson_scripts"]["ADetailer"]["args"]
    face_slot = ad_args[2]
    hand_slot = ad_args[3]

    assert face_slot["ad_use_steps"] is True
    assert face_slot["ad_steps"] >= 14
    assert face_slot["ad_use_inpaint_width_height"] is True
    assert face_slot["ad_inpaint_width"] == 768
    assert face_slot["ad_inpaint_height"] == 768
    assert face_slot["ad_use_cfg_scale"] is False
    assert face_slot["ad_denoising_strength"] == 0.21
    assert face_slot["ad_prompt"] == plan.prompt
    assert face_slot["ad_negative_prompt"] == plan.negative_prompt

    assert hand_slot["ad_use_steps"] is True
    assert hand_slot["ad_steps"] >= 14
    assert hand_slot["ad_use_inpaint_width_height"] is True
    assert hand_slot["ad_inpaint_width"] == 512
    assert hand_slot["ad_inpaint_height"] == 512
    assert hand_slot["ad_use_cfg_scale"] is False
    assert hand_slot["ad_denoising_strength"] == 0.26
    assert hand_slot["ad_mask_blur"] == 8
    assert hand_slot["ad_prompt"] == plan.prompt
    assert hand_slot["ad_negative_prompt"] == plan.negative_prompt


def test_adetailer_dialog_no_prompt_fields_and_passthrough(qapp, isolated_storage):
    from PyQt6.QtWidgets import QLabel
    from qfluentwidgets import LineEdit

    initial_params = {
        "enable_adetailer": True,
        "adetailer_hands_enabled": True,
        "adetailer_hands_prompt": "legacy custom hands",
        "adetailer_hands_negative_prompt": "legacy bad hands"
    }

    dialog = ADetailerSettingsDialog(initial_params)
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

    dialog._on_save_clicked()
    assert dialog.result_params["adetailer_hands_prompt"] == ""
    assert dialog.result_params["adetailer_hands_negative_prompt"] == ""

    # Legacy prompt fields in model JSON must NOT override the main prompt/negative in ArtDirector
    ad = ArtDirector()
    profiles = load_art_director_models()
    assert len(profiles) > 0
    profiles[0]["pipeline"].update(dialog.result_params)
    profiles[0]["pipeline"]["adetailer_hands_prompt"] = "detailed hand, realistic fingers"
    profiles[0]["pipeline"]["adetailer_hands_negative_prompt"] = "woman, girl, body"
    save_art_director_models(profiles)
    ad.reload_models()

    plan = ad.plan_generation(
        raw_prompt="girl drawing with hands",
        preferred_style=profiles[0]["id"],
        mode="assistant"
    )
    forge_kwargs = plan.to_forge_kwargs()
    face_slot = forge_kwargs["alwayson_scripts"]["ADetailer"]["args"][2]
    hand_slot = forge_kwargs["alwayson_scripts"]["ADetailer"]["args"][3]

    assert face_slot["ad_prompt"] == plan.prompt
    assert face_slot["ad_negative_prompt"] == plan.negative_prompt
    assert hand_slot["ad_prompt"] == plan.prompt
    assert hand_slot["ad_negative_prompt"] == plan.negative_prompt
    assert "detailed hand, realistic fingers" not in hand_slot["ad_prompt"]
    assert "woman, girl, body" not in hand_slot["ad_negative_prompt"]


def test_generation_plan_separate_slots_and_skip():
    """Проверка GenerationPlan.to_forge_kwargs() с раздельными ad_prompt и adetailer_hands_prompt (включая [SKIP])."""
    # 1. Обычные раздельные промпты лица и рук
    plan = GenerationPlan(
        prompt="beautiful girl sitting in cafe",
        positive_prompt="beautiful girl sitting in cafe",
        negative_prompt="ugly, bad anatomy",
        enable_adetailer=True,
        adetailer_hands_enabled=True,
        ad_prompt="face closeup, green eyes, smiling",
        ad_negative_prompt="bad face, ugly eyes",
        adetailer_hands_prompt="delicate fingers, holding cup",
        adetailer_hands_negative_prompt="bad hands, extra fingers",
    )
    kwargs = plan.to_forge_kwargs()
    ad_args = kwargs["alwayson_scripts"]["ADetailer"]["args"]
    assert len(ad_args) == 4
    face_slot = ad_args[2]
    hand_slot = ad_args[3]

    assert face_slot["ad_prompt"] == "face closeup, green eyes, smiling"
    assert face_slot["ad_negative_prompt"] == "bad face, ugly eyes"
    assert face_slot["ad_model"] != "None"

    assert hand_slot["ad_prompt"] == "delicate fingers, holding cup"
    assert hand_slot["ad_negative_prompt"] == "bad hands, extra fingers"
    assert hand_slot["ad_model"] != "None"

    # 2. Проверка режима [SKIP] (отключение инпейнта конкретного слота)
    plan_skip = GenerationPlan(
        prompt="landscape with robot back view",
        enable_adetailer=True,
        adetailer_hands_enabled=True,
        ad_prompt="[SKIP]",
        adetailer_hands_prompt="[SKIP]",
    )
    kwargs_skip = plan_skip.to_forge_kwargs()
    args_skip = kwargs_skip["alwayson_scripts"]["ADetailer"]["args"]
    face_skip = args_skip[2]
    hand_skip = args_skip[3]

    assert face_skip["ad_prompt"] == "[SKIP]"
    assert face_skip["ad_model"] == "None"
    assert hand_skip["ad_prompt"] == "[SKIP]"
    assert hand_skip["ad_model"] == "None"


def test_art_director_plan_from_visual_intent_face_and_hands_isolation(isolated_storage):
    """
    Проверка ArtDirector.plan_from_visual_intent() с VisualIntent(prompt=..., face_prompt=..., hands_prompt=...):
    убедиться, что ELARIA_VISUAL_DNA попадает в plan.ad_prompt, но НЕ попадает в plan.adetailer_hands_prompt,
    а ручной override из профиля модели имеет наивысший приоритет.
    """
    from services.art_director.contracts import VisualIntent, TargetSubject
    from services.art_director import ELARIA_VISUAL_DNA

    ad = ArtDirector()
    profiles = load_art_director_models()
    model_id = profiles[0]["id"]

    # 1. Проверка изоляции: ELARIA_VISUAL_DNA в лице, но не в руках
    intent = VisualIntent(
        prompt="1girl, walking in neon street",
        face_prompt="gentle smile, bright violet eyes, soft pale skin",
        hands_prompt="hands in leather gloves, holding umbrella",
        target_subject=TargetSubject.ELARIA,
        aspect_ratio="portrait",
        suggested_model=model_id,
        mode="assistant",
    )

    plan = ad.plan_from_visual_intent(visual_intent=intent, preferred_style=model_id, mode="assistant")

    assert ELARIA_VISUAL_DNA in plan.ad_prompt
    assert "gentle smile" in plan.ad_prompt
    assert ELARIA_VISUAL_DNA not in plan.adetailer_hands_prompt
    assert "leather gloves" in plan.adetailer_hands_prompt
    assert "gentle smile" not in plan.adetailer_hands_prompt

    # 2. Проверка ручного override из профиля модели
    profiles[0]["pipeline"]["adetailer_hands_prompt"] = "tactical gloves, cybernetic fingers"
    profiles[0]["pipeline"]["adetailer_hands_negative_prompt"] = "bare hands, organic fingers"
    save_art_director_models(profiles)
    ad.reload_models()

    plan_override = ad.plan_from_visual_intent(visual_intent=intent, preferred_style=model_id, mode="assistant")
    assert plan_override.adetailer_hands_prompt == "tactical gloves, cybernetic fingers"
    assert plan_override.adetailer_hands_negative_prompt == "tactical gloves, cybernetic fingers" or "organic fingers" in plan_override.adetailer_hands_negative_prompt


def test_stream_filter_and_sanitize_structured_art_response():
    """
    Проверка StreamArtTagFilter и sanitize_assistant_art_and_markdown() на JSON-ответе арт-директора:
    в UI уходит reply_ru, а VisualIntent получает все 3 слоя и правильный aspect_ratio.
    """
    from services.assistant_flow import StreamArtTagFilter, sanitize_assistant_art_and_markdown, prepare_assistant_art_dispatch

    json_payload = {
        "reply_ru": "Секунду, перевожу образ в кадр...",
        "scene_prompt": "1girl, cyberpunk hacker, glowing wires, dark server room, neon cyan rim light",
        "face_prompt": "focused expression, glowing neon eyes, pale skin, wet hair strands",
        "hands_prompt": "typing fingers, cybernetic fingertips, glowing keyboard reflection",
        "negative_prompt": "blurry, low quality",
        "aspect_ratio": "cinematic",
        "suggested_model": "realistic",
        "is_self_portrait": True
    }
    raw_json_str = json.dumps(json_payload, ensure_ascii=False)

    # 1. StreamArtTagFilter: во время стриминга сырой JSON удерживается
    stream_filter = StreamArtTagFilter()
    chunk1, has_trigger1 = stream_filter.feed('{\n  "reply_ru": "Секунду, ')
    assert chunk1 == ""
    assert has_trigger1 is True

    chunk2, has_trigger2 = stream_filter.feed('перевожу образ в кадр..."\n}')
    assert chunk2 == ""
    assert has_trigger2 is True

    # При flush извлекается reply_ru
    flushed_text, has_trigger_flush = stream_filter.flush()
    assert flushed_text == "Секунду, перевожу образ в кадр..."
    assert has_trigger_flush is True

    # 2. sanitize_assistant_art_and_markdown
    clean_text, has_gallery, extracted_prompt = sanitize_assistant_art_and_markdown(raw_json_str, user_requested_art=True)
    assert clean_text == "Секунду, перевожу образ в кадр..."
    assert has_gallery is True
    assert extracted_prompt is not None
    assert "cyberpunk hacker" in extracted_prompt

    # 3. prepare_assistant_art_dispatch -> VisualIntent
    v_intent, style, is_self, target_char = prepare_assistant_art_dispatch(extracted_prompt)
    assert v_intent.prompt == "1girl, cyberpunk hacker, glowing wires, dark server room, neon cyan rim light"
    assert v_intent.face_prompt == "focused expression, glowing neon eyes, pale skin, wet hair strands"
    assert v_intent.hands_prompt == "typing fingers, cybernetic fingertips, glowing keyboard reflection"
    assert v_intent.aspect_ratio == "cinematic"
    assert is_self is True
    assert target_char == "Элария"


def test_adetailer_diagnostic_logging(caplog):
    """
    Проверка диагностического логирования:
    1. parse_structured_art_response: logger.info с флагами (ar, face, hands, self) и warning при fallback.
    2. _format_adetailer_log_blocks: теги [src=json|override|fallback] и статус SKIPPED при [SKIP].
    3. forge_client slots_summary: SlotN(SKIPPED) при ad_model == 'None'.
    """
    import logging
    from services.assistant_flow import parse_structured_art_response
    from services.art_director.plan import GenerationPlan
    from services.art_director.director import ArtDirector
    from services.art_director.contracts import VisualIntent

    caplog.set_level(logging.INFO)

    # 1. parse_structured_art_response
    valid_json = json.dumps({
        "scene_prompt": "1girl, cyberpunk",
        "face_prompt": "smile, glowing eyes",
        "hands_prompt": "slender fingers",
        "aspect_ratio": "portrait",
        "is_self_portrait": True,
    })
    data = parse_structured_art_response(valid_json)
    assert data is not None
    assert "[StructuredArt] JSON parsed successfully: ar=portrait, face=True, hands=True, self=True" in caplog.text

    # Fallback to [IMAGE_PROMPT]
    legacy_prompt = "[IMAGE_PROMPT: 1girl, cyberpunk --ar portrait]"
    caplog.clear()
    data_legacy = parse_structured_art_response(legacy_prompt)
    assert data_legacy is None
    assert "Falling back to [IMAGE_PROMPT] legacy format" in caplog.text

    # 2. _format_adetailer_log_blocks
    # 2a. SKIPPED face & hands
    plan_skipped = GenerationPlan(
        enable_adetailer=True,
        adetailer_hands_enabled=True,
        ad_prompt="[SKIP]",
        adetailer_hands_prompt="[SKIP]",
        ad_source="json",
        adetailer_hands_source="json",
    )
    face_info, hands_info = ArtDirector._format_adetailer_log_blocks(plan_skipped)
    assert "SKIPPED [src=json] | Model=None" in face_info
    assert "SKIPPED [src=json] | Model=None" in hands_info

    # 2b. ON with sources: face=json, hands=override
    plan_override = GenerationPlan(
        enable_adetailer=True,
        adetailer_hands_enabled=True,
        ad_prompt="beautiful detailed face",
        adetailer_hands_prompt="tactical gloves",
        ad_source="json",
        adetailer_hands_source="override",
    )
    face_info, hands_info = ArtDirector._format_adetailer_log_blocks(plan_override)
    assert "ON [src=json]" in face_info
    assert "ON [src=override]" in hands_info

    # 2c. ON with fallback
    plan_fallback = GenerationPlan(
        enable_adetailer=True,
        adetailer_hands_enabled=True,
        ad_source="fallback",
        adetailer_hands_source="fallback",
    )
    face_info, hands_info = ArtDirector._format_adetailer_log_blocks(plan_fallback)
    assert "ON [src=fallback]" in face_info
    assert "ON [src=fallback]" in hands_info

    # 3. forge_client slots_summary with SKIPPED slot
    plan_to_forge = GenerationPlan(
        enable_adetailer=True,
        adetailer_hands_enabled=True,
        ad_prompt="[SKIP]",
        adetailer_hands_prompt="delicate hands",
    )
    kwargs = plan_to_forge.to_forge_kwargs()
    ad_args = kwargs["alwayson_scripts"]["ADetailer"]["args"]
    # ad_args: [True, False, face_slot, hand_slot, ...]
    assert ad_args[2]["ad_model"] == "None"
    assert ad_args[3]["ad_model"] != "None"

    # Simulate ForgeClient slot formatting
    slots_summary = []
    for idx, slot in enumerate(ad_args[2:], start=1):
        if isinstance(slot, dict):
            m = slot.get("ad_model", "none")
            if m == "None":
                slots_summary.append(f"Slot{idx}(SKIPPED)")
            else:
                d = slot.get("ad_denoising_strength", 0.0)
                sz = slot.get("ad_inpaint_width", 0)
                st = slot.get("ad_steps", "auto")
                slots_summary.append(f"Slot{idx}({m}: d={d}, steps={st}, {sz}px)")
    assert slots_summary[0] == "Slot1(SKIPPED)"
    assert "Slot2(" in slots_summary[1] and "SKIPPED" not in slots_summary[1]



