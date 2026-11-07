"""
Unit and integration tests for mobile_server/routes/chat.py and gallery art parameters.
Verifies:
1. Dynamic binding of Gallery Art Pass LLM temperature to active session settings.
2. LM Studio telemetry formatting.
3. Real FastAPI TestClient endpoints for Mobile Gateway (/health and /api/chat/stream).
"""
import json
import logging
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from config import HISTORY_DIR
from mobile_server.api import app
from mobile_server.dependencies import get_lm_studio_client
from mobile_server.schemas import SendMessageRequest
from services.assistant_flow import resolve_gallery_art_temperature
from services.lm_studio import LMStudioClient
from utils.file_utils import atomic_write_json


# =========================================================================
# 1. Резолюция температуры Gallery Art Pass
# =========================================================================

def test_gallery_art_temperature_resolution_from_session():
    """Проверка, что при session['sampling_params']['temperature'] = 0.85 возвращается 0.85."""
    session = {"sampling_params": {"temperature": 0.85}}
    req = SendMessageRequest(session_id="s1", text="тест", mode="assistant")
    profile = {"sampling": {"temperature": 0.70}}

    resolved = resolve_gallery_art_temperature(session=session, req=req, profile=profile)
    assert resolved == pytest.approx(0.85)


def test_gallery_art_temperature_resolution_empty_session():
    """Проверка, что при пустой сессии без оверрайдов выставляется безопасный дефолт 0.8."""
    session = {}
    req = SendMessageRequest(session_id="s1", text="тест", mode="assistant")
    profile = {}

    resolved = resolve_gallery_art_temperature(session=session, req=req, profile=profile)
    assert resolved == pytest.approx(0.8)


def test_gallery_art_temperature_resolution_req_overrides():
    """Проверка приоритета req.sampling_overrides перед profile."""
    session = {}
    req = SendMessageRequest(session_id="s1", text="тест", mode="assistant", sampling_overrides={"temperature": 0.95})
    profile = {"sampling": {"temperature": 0.70}}

    resolved = resolve_gallery_art_temperature(session=session, req=req, profile=profile)
    assert resolved == pytest.approx(0.95)


def test_gallery_art_temperature_resolution_profile_fallback():
    """Проверка fallback на profile.sampling когда в сессии и req нет temperature."""
    session = {}
    req = SendMessageRequest(session_id="s1", text="тест", mode="assistant")
    profile = {"sampling": {"temperature": 0.72}}

    resolved = resolve_gallery_art_temperature(session=session, req=req, profile=profile)
    assert resolved == pytest.approx(0.72)


def test_gallery_art_temperature_resolution_invalid_type_fallback():
    """Проверка безопасного fallback на 0.8 при невалидном значении температуры."""
    session = {"sampling_params": {"temperature": "not_a_float"}}
    resolved = resolve_gallery_art_temperature(session=session)
    assert resolved == pytest.approx(0.8)


# =========================================================================
# 2. Телеметрия параметров инференса LM Studio
# =========================================================================

def test_lms_params_telemetry_reflects_session_temperature(caplog):
    """
    Проверяет, что при вызове lm_client.chat_completion со значением target_temperature
    в строке телеметрии [LMS-PARAMS] фиксируется актуальная температура.
    """
    client = LMStudioClient()
    client._last_loaded_model_id = "test-model"
    client._is_model_unloaded = False
    client._current_loaded_ctx = 16384

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "[IMAGE_PROMPT: art]"}}]
    }

    with patch.object(client, "is_model_loaded", return_value=True), \
         patch.object(client, "get_active_model_id", return_value="test-model"), \
         patch.object(client, "ensure_model_loaded", return_value=True), \
         patch("services.lm_studio._get_vram_telemetry_str", return_value=""), \
         patch.object(client._persistent_session, "post", return_value=mock_resp), \
         caplog.at_level(logging.INFO, logger="NeonRoute.LMStudio"):

        client.chat_completion(
            messages=[{"role": "user", "content": "generate tags"}],
            temperature=0.85,
            stream=False
        )

    assert "[LMS-PARAMS]" in caplog.text
    assert "temp=0.85" in caplog.text
    assert "temp=0.4" not in caplog.text


# =========================================================================
# 3. Настоящие тесты Mobile Server API (FastAPI TestClient)
# =========================================================================

def test_mobile_server_health():
    """Проверяет эндпоинт здоровья мобильного шлюза."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "NeonRoute Mobile Gateway" in data["service"]


def test_chat_stream_validation_error():
    """Проверяет отклонение запроса с невалидной Pydantic-схемой (422)."""
    client = TestClient(app)
    response = client.post("/api/chat/stream", json={"bad_field": 123})
    assert response.status_code == 422


def test_chat_stream_session_not_found():
    """Проверяет возврат 404 при попытке стриминга в несуществующую сессию."""
    client = TestClient(app)
    payload = {
        "session_id": "nonexistent_sess_99999",
        "text": "Привет",
        "mode": "assistant"
    }
    response = client.post("/api/chat/stream", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_chat_stream_success_flow():
    """
    Сквозной тест стриминга SSE:
    создает изолированную сессию, отправляет запрос через TestClient,
    получает SSE-поток (200 OK) и проверяет сохранение реплик в файл сессии.
    """
    test_sid = "test_mobile_stream_session_01"
    session_file = HISTORY_DIR / f"chat_{test_sid}.json"
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    initial_session = {
        "id": test_sid,
        "mode": "assistant",
        "title": "Тестовый мобильный чат",
        "messages": [],
        "chat_history": []
    }
    atomic_write_json(session_file, initial_session)

    mock_lm = MagicMock()
    mock_lm.stream_chat_completion.return_value = [
        "<think>Думаю над ответом</think>",
        "Привет из мобильного API!"
    ]
    mock_lm.chat_completion.return_value = "Привет из мобильного API!"

    app.dependency_overrides[get_lm_studio_client] = lambda: mock_lm

    try:
        client = TestClient(app)
        payload = {
            "session_id": test_sid,
            "text": "Как дела?",
            "mode": "assistant",
            "sampling_overrides": {"temperature": 0.82}
        }

        with client.stream("POST", "/api/chat/stream", json=payload) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
            content = "".join([chunk.decode("utf-8", errors="ignore") if isinstance(chunk, bytes) else str(chunk) for chunk in response.iter_raw()])

            assert "event: token" in content or "event: done" in content
            assert "Привет из мобильного API!" in content

        # Проверяем сохранение реплик на диске в изолированной истории
        with open(session_file, "r", encoding="utf-8") as f:
            saved_session = json.load(f)

        assert len(saved_session["messages"]) == 2
        assert saved_session["messages"][0]["role"] == "user"
        assert saved_session["messages"][0]["content"] == "Как дела?"
        assert saved_session["messages"][1]["role"] == "assistant"
        assert saved_session["messages"][1]["content"] == "Привет из мобильного API!"
        assert saved_session["messages"][1]["thought"] == "Думаю над ответом"
    finally:
        app.dependency_overrides.clear()
        if session_file.exists():
            session_file.unlink()
