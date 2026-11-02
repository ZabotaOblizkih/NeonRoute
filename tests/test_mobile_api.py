"""
Integration and unit tests for NeonRoute Mobile Server API.
"""

import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from config import HISTORY_DIR
from mobile_server.api import app
from mobile_server.dependencies import get_lm_studio_client

client = TestClient(app)


def test_system_status():
    """
    GET /api/system/status returns 200, valid service statuses and hardware telemetry.
    """
    response = client.get("/api/system/status")
    assert response.status_code == 200, response.text
    data = response.json()

    # 1. Services verification
    assert "services" in data
    assert isinstance(data["services"], list)
    ports = {s["port"] for s in data["services"]}
    assert 1234 in ports  # LM Studio
    assert 7860 in ports  # SD Forge
    assert 5010 in ports  # TTS

    for s in data["services"]:
        assert isinstance(s["port"], int)
        assert isinstance(s["is_online"], bool)
        assert isinstance(s["name"], str)

    # 2. Telemetry verification
    assert "telemetry" in data
    telem = data["telemetry"]
    assert isinstance(telem["gpu_name"], str)
    assert isinstance(telem["vram_used_mb"], (int, float))
    assert isinstance(telem["vram_total_mb"], (int, float))
    assert isinstance(telem["vram_percent"], (int, float))
    assert isinstance(telem["ram_used_mb"], (int, float))
    assert isinstance(telem["ram_total_mb"], (int, float))
    assert isinstance(telem["ram_percent"], (int, float))

    assert telem["vram_total_mb"] >= 0
    assert telem["ram_total_mb"] > 0
    assert 0.0 <= telem["ram_percent"] <= 100.0

    # 3. Active mode
    assert "active_mode" in data
    assert data["active_mode"] in ("assistant", "roleplay", "engineer")


def test_system_mode_switch():
    """
    POST /api/system/mode switches active mode cleanly without GUI rebuild.
    """
    # Switch to roleplay
    res_rp = client.post("/api/system/mode", json={"mode": "roleplay"})
    assert res_rp.status_code == 200
    assert res_rp.json()["active_mode"] == "roleplay"

    # Switch to engineer
    res_eng = client.post("/api/system/mode", json={"mode": "engineer"})
    assert res_eng.status_code == 200
    assert res_eng.json()["active_mode"] == "engineer"

    # Invalid mode rejection
    res_inv = client.post("/api/system/mode", json={"mode": "invalid_mode"})
    assert res_inv.status_code == 400

    # Return to assistant
    res_asst = client.post("/api/system/mode", json={"mode": "assistant"})
    assert res_asst.status_code == 200
    assert res_asst.json()["active_mode"] == "assistant"


def test_session_lifecycle():
    """
    Tests complete lifecycle: create session -> get details -> list in index -> delete session.
    """
    # 1. Create session
    create_res = client.post(
        "/api/sessions",
        json={"mode": "assistant", "title": "Mobile Test Lifecycle Session"},
    )
    assert create_res.status_code == 200, create_res.text
    create_data = create_res.json()
    assert create_data["success"] is True
    session_id = create_data["session_id"]
    assert session_id

    session_file = HISTORY_DIR / f"chat_{session_id}.json"
    assert session_file.exists(), f"Expected session file {session_file} to exist on disk."

    try:
        # 2. Get session details
        detail_res = client.get(f"/api/sessions/{session_id}")
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert detail["id"] == session_id
        assert detail["title"] == "Mobile Test Lifecycle Session"
        assert detail["mode"] == "assistant"
        assert isinstance(detail["messages"], list)
        assert len(detail["messages"]) == 0
        assert isinstance(detail["sampling_params"], dict)

        # 3. List sessions
        list_res = client.get("/api/sessions")
        assert list_res.status_code == 200
        sessions = list_res.json()
        matched = [s for s in sessions if s["id"] == session_id]
        assert len(matched) == 1
        assert matched[0]["title"] == "Mobile Test Lifecycle Session"
        assert matched[0]["message_count"] == 0

    finally:
        # 4. Delete session
        del_res = client.delete(f"/api/sessions/{session_id}")
        assert del_res.status_code == 200
        assert del_res.json()["success"] is True

        # Check file was deleted
        assert not session_file.exists(), f"Session file {session_file} was not removed after delete."

        # Verify subsequent GET returns 404
        get_again = client.get(f"/api/sessions/{session_id}")
        assert get_again.status_code == 404


def test_404_handling():
    """
    Verify 404 responses return valid JSON error messages for missing sessions.
    """
    fake_id = "non_existent_session_id_404"
    res_get = client.get(f"/api/sessions/{fake_id}")
    assert res_get.status_code == 404
    assert res_get.json() == {"detail": "Session not found"}

    res_del = client.delete(f"/api/sessions/{fake_id}")
    assert res_del.status_code == 404
    assert res_del.json() == {"detail": "Session not found"}


def test_chat_stream_structure():
    """
    Tests SSE stream structure, thought/token tag parsing, and session persistence.
    """
    # 1. Create a session for this chat test
    create_res = client.post(
        "/api/sessions",
        json={"mode": "assistant", "title": "Stream Test Session"},
    )
    assert create_res.status_code == 200
    session_id = create_res.json()["session_id"]
    session_file = HISTORY_DIR / f"chat_{session_id}.json"

    # Mock token sequence containing <think>...</think> block
    mock_chunks = [
        "<think>",
        "Thinking about the user request.",
        "</think>",
        "Hello! ",
        "I am ready to help you.",
    ]

    mock_client = MagicMock()
    mock_client.stream_chat_completion.return_value = iter(mock_chunks)

    app.dependency_overrides[get_lm_studio_client] = lambda: mock_client

    try:
        # Call /api/chat/stream with TestClient streaming
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={
                "session_id": session_id,
                "text": "Hello, can you help me?",
                "mode": "assistant",
            },
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            lines = [line.strip() for line in response.iter_lines() if line.strip()]

        events = []
        current_event = None
        for line in lines:
            if line.startswith("event:"):
                current_event = line.replace("event:", "").strip()
            elif line.startswith("data:") and current_event:
                data_val = line.replace("data:", "").strip()
                events.append((current_event, data_val))
                current_event = None

        event_names = [ev[0] for ev in events]
        assert "thought" in event_names
        assert "thought_end" in event_names
        assert "token" in event_names
        assert "done" in event_names

        # Verify thought content
        thought_data = "".join(data for ev, data in events if ev == "thought")
        assert "Thinking about the user request." in thought_data

        # Verify tokens content
        tokens_data = "".join(data for ev, data in events if ev == "token")
        assert "Hello!" in tokens_data
        assert "I am ready to help you." in tokens_data

        # Verify done payload
        done_data = [data for ev, data in events if ev == "done"][0]
        assert "Hello! I am ready to help you." in done_data

        # Verify history saved to disk
        assert session_file.exists()
        with open(session_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)

        messages = saved_data.get("messages", [])
        assert len(messages) >= 2

        # User message
        user_msg = messages[-2]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "Hello, can you help me?"

        # Assistant message
        asst_msg = messages[-1]
        assert asst_msg["role"] == "assistant"
        assert "Hello! I am ready to help you." in asst_msg["content"]
        assert asst_msg.get("thought") == "Thinking about the user request."

    finally:
        app.dependency_overrides.clear()
        client.delete(f"/api/sessions/{session_id}")


def test_serve_root_index():
    """
    GET / returns 200, content-type text/html, and contains root app container.
    """
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert '<div id="app">' in res.text
    assert 'class="app-header"' in res.text


def test_serve_static_manifest():
    """
    GET /static/manifest.json returns 200 and valid JSON with display: standalone.
    """
    res = client.get("/static/manifest.json")
    assert res.status_code == 200
    data = res.json()
    assert data.get("display") == "standalone"
    assert data.get("short_name") == "NeonRoute"
    assert "icons" in data
    assert len(data["icons"]) >= 2


def test_serve_static_sw():
    """
    GET /static/sw.js returns 200 and javascript content-type.
    """
    res = client.get("/static/sw.js")
    assert res.status_code == 200
    ct = res.headers.get("content-type", "").lower()
    assert "javascript" in ct
    assert "neonroute-shell" in res.text


def test_get_characters():
    """
    GET /api/characters returns 200 and valid character cards with id, name, archetype.
    """
    res = client.get("/api/characters")
    assert res.status_code == 200, res.text
    characters = res.json()
    assert isinstance(characters, list)
    if characters:
        char = characters[0]
        assert "id" in char
        assert "name" in char
        assert "avatar_url" in char
        assert "archetype" in char


def test_forge_models():
    """
    GET /api/art/models returns list containing auto model, and POST /api/art/active-model sets model.
    """
    res = client.get("/api/art/models")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "models" in data
    assert "active_model_id" in data
    model_ids = [m["id"] for m in data["models"]]
    assert "auto" in model_ids

    # Set active model
    set_res = client.post("/api/art/active-model", json={"model_id": "auto"})
    assert set_res.status_code == 200
    assert set_res.json()["success"] is True


def test_session_cast():
    """
    GET /api/sessions/{session_id}/cast returns structured roleplay state.
    """
    create_res = client.post("/api/sessions", json={"mode": "roleplay", "title": "Cast Test"})
    assert create_res.status_code == 200
    session_id = create_res.json()["session_id"]

    try:
        res = client.get(f"/api/sessions/{session_id}/cast")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["session_id"] == session_id
        assert "location" in data
        assert "quest" in data
        assert "inventory" in data
        assert "stats" in data
    finally:
        client.delete(f"/api/sessions/{session_id}")


def test_media_avatar():
    """
    GET /media/avatar/{session_id} and /media/character-avatar/{char_id} return image or SVG fallback.
    """
    res = client.get("/media/avatar/non_existent_session_123")
    assert res.status_code == 200
    assert "image" in res.headers.get("content-type", "")

    res_char = client.get("/media/character-avatar/non_existent_character_123")
    assert res_char.status_code == 200
    assert "image" in res_char.headers.get("content-type", "")


def test_unload_vram():
    """
    POST /api/system/unload-vram executes VRAM purge and returns 200.
    """
    with patch("services.lm_studio.LMStudioClient.unload_models_cli", return_value={"success": True}):
        with patch("services.forge_client.ForgeClient.unload_checkpoint", return_value=True):
            res = client.post("/api/system/unload-vram")
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert "details" in data


