import pytest
import uuid
from unittest.mock import MagicMock, patch
from pathlib import Path

from services.session_manager import SessionManager
from services.forge_client import ForgeClient
from services.sandbox_guard import install_guard
from config import WORKSPACE_DIR


def test_session_manager_load_session_adds_missing_ids(tmp_path):
    sess_id = f"test_{uuid.uuid4().hex[:6]}"
    file_path = tmp_path / f"chat_{sess_id}.json"
    data = {
        "id": sess_id,
        "mode": "assistant",
        "messages": [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "hi", "id": "pre-existing-id"},
            {"role": "user", "text": "how are you"}
        ]
    }
    import json
    file_path.write_text(json.dumps(data), encoding="utf-8")

    with patch("services.session_manager.HISTORY_DIR", tmp_path):
        loaded = SessionManager.load_session(sess_id)
        assert loaded is not None
        msgs = loaded["messages"]
        assert len(msgs) == 3
        assert "id" in msgs[0] and len(msgs[0]["id"]) > 0
        assert msgs[1]["id"] == "pre-existing-id"
        assert "id" in msgs[2] and len(msgs[2]["id"]) > 0
        assert msgs[0]["id"] != msgs[2]["id"]


def test_session_manager_get_lm_studio_messages_flattens_old_multimodal():
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Смотри на это фото"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
            ]
        },
        {
            "role": "assistant",
            "content": "Красивое фото!"
        },
        {
            "role": "user",
            "content": "А теперь ответь на текстовый вопрос"
        }
    ]

    # When vision_content is None (text model turn)
    res = SessionManager.get_lm_studio_messages(history=history, vision_content=None)
    # The first message should be flattened to string
    user_msgs = [m for m in res if m.get("role") == "user"]
    assert isinstance(user_msgs[0]["content"], str)
    assert "Смотри на это фото" in user_msgs[0]["content"]
    assert "data:image/png;base64" not in user_msgs[0]["content"]


def test_forge_client_unload_checkpoint_idempotency():
    client = ForgeClient()
    client._checkpoint_unloaded = True

    with patch("requests.post") as mock_post:
        res = client.unload_checkpoint()
        assert res is True
        mock_post.assert_not_called()


def test_sandbox_guard_remove_outside_workspace():
    install_guard()
    # Attempting to delete a file outside workspace should raise PermissionError
    import sys
    outside_file = Path("c:/NeonRoute/outside_test.txt")
    with pytest.raises(PermissionError):
        sys.audit("os.remove", outside_file, None)

    # File inside workspace should not raise in audit hook
    inside_file = WORKSPACE_DIR.resolve() / "test_file.txt"
    try:
        sys.audit("os.remove", inside_file, None)
    except PermissionError:
        pytest.fail("sys.audit raised PermissionError for a path inside WORKSPACE_DIR")


def test_forge_client_txt2img_sanitization_and_typing():
    import base64
    client = ForgeClient()
    test_out = WORKSPACE_DIR / "test_forge_out"
    test_out.mkdir(parents=True, exist_ok=True)

    # Dummy image payload (> 100 bytes)
    dummy_png_b64 = base64.b64encode(b"PNG_TEST_HEADER_" + b"x" * 150).decode("ascii")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "images": [dummy_png_b64],
        "info": '{"seed": 12345}'
    }

    try:
        with patch.object(client, "is_server_ready", return_value=True), \
             patch.object(client, "get_current_model", return_value="v1-5-pruned"), \
             patch("requests.Session.post", return_value=mock_resp) as mock_post:

            saved_path = client.text_to_image(
                prompt="masterpiece,\r\n<lora:test_lora:0.8>,\r\n1girl\t\t\nsolo",
                negative_prompt="bad hands,\r\nblurry\n\n",
                seed=-1.0,
                steps=25,
                cfg_scale=7,
                width=500,
                height=30,
                output_dir=test_out
            )

            assert mock_post.called
            call_kwargs = mock_post.call_args.kwargs
            assert call_kwargs.get("timeout") >= 120
            payload = call_kwargs.get("json", {})

            # 1. Prompt sanitization preserves LoRA tags without \r, \n, tabs
            assert payload["prompt"] == "masterpiece, <lora:test_lora:0.8>, 1girl solo"
            assert payload["negative_prompt"] == "bad hands, blurry"

            # 2. Strict numeric typing & sampling params
            assert isinstance(payload["seed"], int)
            assert payload["seed"] == -1
            assert isinstance(payload["steps"], int)
            assert payload["steps"] == 25
            assert isinstance(payload["cfg_scale"], float)
            assert payload["cfg_scale"] == 7.0

            # 3. Width & height multiple of 64 and at least 64
            assert isinstance(payload["width"], int)
            assert payload["width"] == 448  # (500 // 64) * 64
            assert isinstance(payload["height"], int)
            assert payload["height"] == 64  # max(64, (30 // 64) * 64)

            # 4. Return value is a Path to the saved image file
            assert isinstance(saved_path, Path)
            assert saved_path.exists()
    finally:
        if test_out.exists():
            import shutil
            shutil.rmtree(test_out, ignore_errors=True)

