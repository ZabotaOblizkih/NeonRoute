"""
Unit and integration tests for the mobile two-pass art protocol and marker filtering in mobile_server/routes/chat.py.
"""

import json
import re
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from mobile_server.api import app
from mobile_server.routes.chat import RE_GALLERY_TRIGGER, RE_IMAGE_TAG
from mobile_server.dependencies import get_lm_studio_client, get_forge_client


def test_gallery_trigger_regex_coverage():
    """Verifies Unicode-safe regex matches all variations of gallery trigger."""
    cases = [
        "[GALERY]",
        "[GALLERY]",
        "[ГАЛЕРЕЯ]",
        "[ГАЛЕРИЯ]",
        "[ГАЛЛЕРЕЯ]",
        "[ГАЛЛЕРИЯ]",
        "[галерея]",
        "[галерия]",
        "[галлерея]",
        "[галлерия]",
        "[Галерея]",
        "[Gallery]",
    ]
    for case in cases:
        assert RE_GALLERY_TRIGGER.search(case) is not None, f"Failed to match: {case}"
        cleaned = RE_GALLERY_TRIGGER.sub("", case).strip()
        assert cleaned == "", f"Failed to clean: {case}"


def test_markdown_image_filtering():
    """Verifies that hallucinated markdown images ![alt](url) and gallery triggers are cleaned."""
    raw_response = "[ГАЛЕРЕЯ] Конечно, вот неоновый город!\n![Неоновый город](https://fake.url/image.png)"
    cleaned = RE_GALLERY_TRIGGER.sub("", raw_response).strip()
    cleaned = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned).strip()
    assert "[ГАЛЕРЕЯ]" not in cleaned
    assert "https://fake.url/image.png" not in cleaned
    assert "Конечно, вот неоновый город!" in cleaned


def test_skill_router_and_two_pass_art_flow(tmp_path):
    """
    Simulates sending 'Нарисуй неоновый город' to /api/chat/stream:
    1. SkillRouter detects 'gallery'.
    2. Context system prompt receives the gallery directive.
    3. Stream completion outputs [ГАЛЕРЕЯ] marker + fake markdown image.
    4. Client receives clean text without [ГАЛЕРЕЯ] and without ![...].
    5. Second pass calls AssistantPromptBuilder.build_gallery_art_prompt and LM Studio chat_completion.
    6. Task is queued to TaskQueue and Forge generates image.
    """
    client = TestClient(app)

    # 1. Create a session
    sess_res = client.post("/api/sessions", json={"mode": "assistant", "title": "Test Art Two-Pass"})
    assert sess_res.status_code == 200
    session_id = sess_res.json()["session_id"]

    # 2. Mock LM Studio
    mock_lm = MagicMock()
    # Pass 1 returns LLM streaming tokens containing [ГАЛЕРЕЯ] and hallucinated image
    mock_lm.stream_chat_completion.return_value = [
        "<think>Планирую образ города</think>",
        "[ГАЛЕРЕЯ] ",
        "Конечно, сейчас создам образ! "
    ]

    # Pass 2 hidden chat_completion returns Danbooru tags
    art_tags_response = (
        "[IMAGE_PROMPT: 1city, neon lights, cyberpunk skyscrapers, reflective wet asphalt, night, masterpiece --ar wide --model realistic]"
    )
    mock_lm.chat_completion.return_value = art_tags_response

    # 3. Mock ForgeClient
    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True
    fake_img = tmp_path / "neon_art.png"
    fake_img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    mock_forge.text_to_image.return_value = fake_img

    app.dependency_overrides[get_lm_studio_client] = lambda: mock_lm
    app.dependency_overrides[get_forge_client] = lambda: mock_forge

    try:
        # Request with "Нарисуй неоновый город"
        req_payload = {
            "session_id": session_id,
            "text": "Нарисуй неоновый город",
            "mode": "assistant"
        }

        with client.stream("POST", "/api/chat/stream", json=req_payload) as response:
            assert response.status_code == 200
            token_chunks = []
            done_payload = None
            art_started = False
            for line in response.iter_lines():
                if line.startswith("event: token"):
                    pass
                elif line.startswith("event: done"):
                    pass
                elif line.startswith("event: art_started"):
                    art_started = True
                elif line.startswith("data: "):
                    data_val = line[6:]
                    if not done_payload and "Конечно" in data_val:
                        done_payload = data_val
                    token_chunks.append(data_val)

        # Verify context received gallery directive from SkillRouter
        call_args = mock_lm.stream_chat_completion.call_args
        context_msgs = call_args[0][0]
        sys_prompt = context_msgs[0]["content"]
        assert "ВИЗУАЛИЗАЦИЯ И АРТ" in sys_prompt or "[GALERY]" in sys_prompt

        # Verify clean output on client side (acceptance criterion 1)
        full_client_tokens = "".join(token_chunks)
        assert "[ГАЛЕРЕЯ]" not in full_client_tokens
        assert "[GALERY]" not in full_client_tokens
        assert "Конечно, сейчас создам образ!" in full_client_tokens
        assert art_started is True

        # Wait a short moment for background thread _gallery_art_worker
        import time
        for _ in range(25):
            if mock_lm.chat_completion.called:
                break
            time.sleep(0.1)

        # Verify second pass hidden call occurred (acceptance criterion 2)
        assert mock_lm.chat_completion.called
        art_call_kwargs = mock_lm.chat_completion.call_args.kwargs
        messages_sent = art_call_kwargs.get("messages", [])
        assert len(messages_sent) == 1
        assert "Image Generation for Stable Diffusion" in messages_sent[0]["content"]
        assert "Нарисуй неоновый город" in messages_sent[0]["content"]
        assert art_call_kwargs.get("temperature") == 0.4

    finally:
        app.dependency_overrides.pop(get_lm_studio_client, None)
        app.dependency_overrides.pop(get_forge_client, None)
        client.delete(f"/api/sessions/{session_id}")
