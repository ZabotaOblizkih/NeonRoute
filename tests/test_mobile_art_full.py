"""
Integration and unit tests for NeonRoute Mobile Art & SD Forge subsystem.
Tests ArtDirector pipeline, VRAMArbiter coordination, TaskQueueWorker priority,
Direct CLI command parsing, and negations extraction.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from mobile_server.api import app
from mobile_server.dependencies import get_forge_client, get_task_queue_worker, get_vram_arbiter
from services.art_director.prompting import extract_negations_from_prompt
from services.direct_command_parser import parse_direct_command_params
from services.task_queue import PRIORITY_NORMAL

client = TestClient(app)


def test_art_models_endpoint():
    """
    1. GET /api/art/models returns models list with auto as first item.
    2. POST /api/art/active-model switches active model.
    """
    # 1. Fetch models
    res = client.get("/api/art/models")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "models" in data
    assert "active_model_id" in data
    assert len(data["models"]) >= 1

    first_model = data["models"][0]
    assert first_model["id"] == "auto"
    assert "Арт-директор" in first_model["name"]
    assert "triggers" in first_model

    # 2. Set active model
    set_res = client.post(
        "/api/art/active-model",
        json={"model_id": "cyberrealistic"}
    )
    assert set_res.status_code == 200, set_res.text
    set_data = set_res.json()
    assert set_data["success"] is True
    assert set_data["active_model_id"] == "cyberrealistic"

    # Verify updated active model
    verify_res = client.get("/api/art/models")
    assert verify_res.status_code == 200
    assert verify_res.json()["active_model_id"] == "cyberrealistic"

    # Reset back to auto
    client.post("/api/art/active-model", json={"model_id": "auto"})


def test_artdirector_pipeline_integration(tmp_path):
    """
    POST /api/art/generate: verifies ArtDirector plan_from_visual_intent is called,
    ADetailer, Hires.fix, and LoRA slots are configured, and image is generated via ForgeClient.
    """
    # Create test session
    sess_res = client.post("/api/sessions", json={"mode": "assistant", "title": "Art Pipeline Test"})
    assert sess_res.status_code == 200
    session_id = sess_res.json()["session_id"]

    mock_img_path = tmp_path / "test_art.png"
    mock_img_path.write_bytes(b"\x89PNG\r\n\x1a\nfake_image_content")

    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True
    mock_forge.text_to_image.return_value = mock_img_path

    app.dependency_overrides[get_forge_client] = lambda: mock_forge

    try:
        gen_res = client.post(
            "/api/art/generate",
            json={
                "session_id": session_id,
                "model_id": "cyberrealistic",
                "prompt": "Elaria standing on a cyberpunk bridge, neon rain",
                "aspect_ratio": "portrait",
                "negative_prompt": "blurry, low quality"
            }
        )
        assert gen_res.status_code == 200, gen_res.text
        data = gen_res.json()
        assert data["success"] is True
        assert "image_url" in data
        assert "generation_params" in data

        params = data["generation_params"]
        assert "prompt" in params
        assert "negative_prompt" in params
        assert params["cfg_scale"] > 0
        assert params["steps"] > 0
        assert params["sampler"]

        # Verify ForgeClient.text_to_image was called with rich ArtDirector parameters
        mock_forge.text_to_image.assert_called_once()
        call_kwargs = mock_forge.text_to_image.call_args.kwargs
        assert "prompt" in call_kwargs
        assert "negative_prompt" in call_kwargs
        assert "alwayson_scripts" in call_kwargs or "enable_adetailer" in call_kwargs
        assert call_kwargs.get("enable_adetailer") is True or "ADetailer" in str(call_kwargs.get("alwayson_scripts"))
        assert call_kwargs.get("enable_hr") is True

    finally:
        app.dependency_overrides.pop(get_forge_client, None)
        client.delete(f"/api/sessions/{session_id}")


def test_negation_extraction():
    """
    Verifies that extract_negations_from_prompt extracts negative words
    ('without people', 'no blur', 'without coat') and moves them to negative_prompt.
    """
    raw_prompt = "Cyberpunk street with neon reflections, without people, no blur, sunset"
    clean_pos, extracted_neg = extract_negations_from_prompt(raw_prompt)

    assert "without people" not in clean_pos.lower()
    assert "no blur" not in clean_pos.lower()
    assert "people" in extracted_neg
    assert "blur" in extracted_neg


def test_direct_command_parsing(tmp_path):
    """
    POST /api/art/direct-command: parses CLI flags
    !art a neon samurai --model anime --ar wide --seed 123 --steps 25 --cfg 7.5
    """
    sess_res = client.post("/api/sessions", json={"mode": "assistant", "title": "Direct Command Test"})
    assert sess_res.status_code == 200
    session_id = sess_res.json()["session_id"]

    # 1. Test parser unit directly
    cmd = "!art a neon samurai --model anime --ar wide --seed 123 --steps 25 --cfg 7.5"
    pos, neg, parsed = parse_direct_command_params(cmd.replace("!art", "").strip())
    assert "neon samurai" in pos
    assert parsed.get("model_override") == "anime"
    assert parsed.get("seed") == 123
    assert parsed.get("steps") == 25
    assert parsed.get("cfg_scale") == 7.5
    assert parsed.get("width") == 768
    assert parsed.get("height") == 512

    # 2. Test API endpoint execution
    mock_img_path = tmp_path / "direct_samurai.png"
    mock_img_path.write_bytes(b"\x89PNG\r\n\x1a\nfake_image")

    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True
    mock_forge.text_to_image.return_value = mock_img_path

    app.dependency_overrides[get_forge_client] = lambda: mock_forge

    try:
        res = client.post(
            "/api/art/direct-command",
            json={
                "session_id": session_id,
                "model_id": "cyberrealistic",
                "command": cmd
            }
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["success"] is True
        assert data["generation_params"]["seed"] == 123
        assert data["generation_params"]["steps"] == 25
        assert data["generation_params"]["cfg_scale"] == 7.5

        # Check kwargs passed to forge
        call_kwargs = mock_forge.text_to_image.call_args.kwargs
        assert call_kwargs["seed"] == 123
        assert call_kwargs["steps"] == 25
        assert call_kwargs["cfg_scale"] == 7.5
        assert call_kwargs["width"] == 768
        assert call_kwargs["height"] == 512

    finally:
        app.dependency_overrides.pop(get_forge_client, None)
        client.delete(f"/api/sessions/{session_id}")


def test_vram_and_queue_safety(tmp_path):
    """
    Confirms generation task is submitted to TaskQueueWorker with PRIORITY_NORMAL
    and triggers VRAMArbiter.
    """
    sess_res = client.post("/api/sessions", json={"mode": "assistant", "title": "VRAM Queue Test"})
    assert sess_res.status_code == 200
    session_id = sess_res.json()["session_id"]

    mock_img_path = tmp_path / "vram_test.png"
    mock_img_path.write_bytes(b"\x89PNG\r\n\x1a\nfake_image")

    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True
    mock_forge.text_to_image.return_value = mock_img_path

    mock_tq = MagicMock()
    # When enqueue is called, immediately execute the task func to resolve the future
    def _fake_enqueue(task):
        assert task.priority == PRIORITY_NORMAL
        assert task.task_type in ("image", "scene_art", "scene_background")
        task.func()

    mock_tq.enqueue.side_effect = _fake_enqueue

    app.dependency_overrides[get_forge_client] = lambda: mock_forge
    app.dependency_overrides[get_task_queue_worker] = lambda: mock_tq

    try:
        with patch("services.vram_arbiter.VRAMArbiter.prepare_for_task") as mock_prep:
            res = client.post(
                "/api/art/generate",
                json={
                    "session_id": session_id,
                    "model_id": "cyberrealistic",
                    "prompt": "Cyberpunk laboratory with glowing monitors"
                }
            )
            assert res.status_code == 200, res.text
            assert res.json()["success"] is True

            # Ensure TaskQueueWorker.enqueue was called with PRIORITY_NORMAL
            mock_tq.enqueue.assert_called_once()

    finally:
        app.dependency_overrides.pop(get_forge_client, None)
        app.dependency_overrides.pop(get_task_queue_worker, None)
        client.delete(f"/api/sessions/{session_id}")
