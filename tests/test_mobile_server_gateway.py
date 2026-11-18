"""
Integration and unit tests for Stage 1 Mobile Gateway deduplication & service upgrades.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from fastapi.testclient import TestClient

from mobile_server.api import app
from mobile_server.dependencies import (
    get_art_director,
    get_forge_client,
    get_health_checker,
    get_lm_studio_client,
    get_system_monitor,
    get_task_queue_worker,
    get_vram_arbiter,
)
from services.art_director.director import ArtDirector
from services.art_director.contracts import TargetSubject
from services.art_director.plan import GenerationPlan
from services.assistant_flow import build_assistant_visual_intent
from services.forge_client import ForgeClient
from services.lm_studio import LMStudioClient
from services.mode_coordinator import AppMode
from services.session_manager import SessionManager
from services.vram_arbiter import GPUOwner, VRAMArbiter
from utils.health_checker import HealthChecker
from utils.system_monitor import SystemMonitor


def test_app_mode_from_session_variants():
    assert AppMode.from_session({"mode": "assistant"}) == AppMode.ASSISTANT
    assert AppMode.from_session({"mode": "assistant", "type": "engineering"}) == AppMode.ENGINEER
    assert AppMode.from_session({"mode": "roleplay"}) == AppMode.ROLEPLAY
    assert AppMode.from_session({"mode": "assistant"}, mode_override="engineer") == AppMode.ENGINEER


def test_lm_studio_native_stream_chat_completion():
    client = LMStudioClient()

    def _fake_chat_completion(**kwargs):
        cb = kwargs.get("chunk_callback")
        assert kwargs.get("stream") is True
        assert kwargs.get("emit_signal") is False
        if cb:
            cb("Hello ")
            cb("world")
        return "Hello world"

    with patch.object(client, "chat_completion", side_effect=_fake_chat_completion):
        tokens = list(client.stream_chat_completion([{"role": "user", "content": "hi"}], temperature=0.5))

    assert tokens == ["Hello ", "world"]


def test_forge_client_get_progress_parsing():
    fc = ForgeClient()
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "progress": 0.42,
        "eta_relative": 5.2,
        "state": {
            "sampling_step": 12,
            "sampling_steps": 28,
            "textinfo": "ADetailer face_yolov8n",
        },
    }
    with patch("services.forge_client.requests.get", return_value=fake_resp):
        snap = fc.get_progress(timeout=0.5)

    assert snap["step"] == 12
    assert snap["total_steps"] == 28
    assert snap["percent"] == 42
    assert "ADetailer" in snap["stage_name"] or "лица" in snap["stage_name"]


def test_vram_arbiter_release_all_resets_owner():
    mock_lm = MagicMock()
    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)
    arbiter._current_owner = GPUOwner.LLM

    details = arbiter.release_all()
    assert details["lm_studio"] == "unloaded"
    assert details["forge"] == "unloaded"
    assert arbiter._current_owner == GPUOwner.NONE
    mock_lm.unload_models_cli.assert_called_once()
    mock_forge.unload_checkpoint.assert_called_once()
    mock_forge.free_vram.assert_called_once()


def test_health_and_system_public_snapshots():
    hc = HealthChecker()
    with patch.object(hc, "_perform_health_checks", return_value={"lm_ok": True, "forge_ok": False, "tts_ok": False}):
        assert hc.get_health_snapshot()["lm_ok"] is True

    sm = SystemMonitor()
    with patch.object(sm, "_poll_hardware", return_value={"vram_used_mb": 100, "ram_used_mb": 4096.0}):
        assert sm.get_hardware_snapshot()["ram_used_mb"] == 4096.0


def test_system_status_and_unload_vram_endpoints():
    mock_hc = MagicMock()
    mock_hc.get_health_snapshot.return_value = {"lm_ok": True, "forge_ok": False, "tts_ok": True}
    mock_sm = MagicMock()
    mock_sm.get_hardware_snapshot.return_value = {
        "gpu_name": "RTX 4060",
        "vram_used_mb": 2048.0,
        "vram_total_mb": 8192.0,
        "vram_percent": 25.0,
        "ram_used_mb": 8000.0,
        "ram_total_mb": 16000.0,
        "ram_percent": 50.0,
    }
    mock_arbiter = MagicMock()
    mock_arbiter.release_all.return_value = {"lm_studio": "unloaded", "forge": "skipped"}

    app.dependency_overrides[get_health_checker] = lambda: mock_hc
    app.dependency_overrides[get_system_monitor] = lambda: mock_sm
    app.dependency_overrides[get_vram_arbiter] = lambda: mock_arbiter

    try:
        client = TestClient(app)
        res_status = client.get("/api/system/status")
        assert res_status.status_code == 200
        body = res_status.json()
        assert body["telemetry"]["ram_used_mb"] == 8000.0
        assert body["services"][0]["is_online"] is True

        res_unload = client.post("/api/system/unload-vram")
        assert res_unload.status_code == 200
        assert res_unload.json()["details"]["lm_studio"] == "unloaded"
        mock_arbiter.release_all.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_sessions_crud_and_message_count(tmp_path, monkeypatch):
    hist_dir = tmp_path / "history"
    out_dir = tmp_path / "outputs"
    hist_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("services.session_manager.HISTORY_DIR", hist_dir)
    monkeypatch.setattr("services.session_manager.OUTPUTS_DIR", out_dir)
    monkeypatch.setattr("mobile_server.routes.sessions.HISTORY_DIR", hist_dir)
    monkeypatch.setattr("services.memory_db.memory_db.db_path", tmp_path / "memory.db")
    monkeypatch.setattr(SessionManager, "_sessions_cache", {})
    monkeypatch.setattr(SessionManager, "_current_session", None)

    client = TestClient(app)

    # Create
    res_create = client.post("/api/sessions", json={"mode": "assistant", "title": "Тест CRUD"})
    assert res_create.status_code == 200
    sid = res_create.json()["session_id"]

    # List
    res_list = client.get("/api/sessions")
    assert res_list.status_code == 200
    items = res_list.json()
    assert any(i["id"] == sid and i["message_count"] == 0 for i in items)

    # Detail
    res_detail = client.get(f"/api/sessions/{sid}")
    assert res_detail.status_code == 200
    assert res_detail.json()["title"] == "Тест CRUD"

    # Avatar ETag 304
    res_av1 = client.get(f"/media/avatar/{sid}")
    assert res_av1.status_code == 200
    etag = res_av1.headers.get("etag")
    assert etag
    res_av2 = client.get(f"/media/avatar/{sid}", headers={"If-None-Match": etag})
    assert res_av2.status_code == 304

    # Delete
    res_del = client.delete(f"/api/sessions/{sid}")
    assert res_del.status_code == 200
    assert not (hist_dir / f"chat_{sid}.json").exists()


def test_chat_stream_and_regenerate_use_task_queue(tmp_path, monkeypatch):
    hist_dir = tmp_path / "history"
    out_dir = tmp_path / "outputs"
    hist_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("services.session_manager.HISTORY_DIR", hist_dir)
    monkeypatch.setattr("services.session_manager.OUTPUTS_DIR", out_dir)
    monkeypatch.setattr("services.memory_db.memory_db.db_path", tmp_path / "memory.db")
    monkeypatch.setattr(SessionManager, "_sessions_cache", {})
    monkeypatch.setattr(SessionManager, "_current_session", None)

    sess = SessionManager.create_session(mode="assistant", title="Очередь")
    sid = sess["id"]
    SessionManager.save_session(sess)

    mock_lm = MagicMock()
    mock_lm.stream_chat_completion.return_value = ["Ответ 1"]

    recorded_tasks = []

    class _SyncTaskQueue:
        current_task = None

        def enqueue(self, task):
            recorded_tasks.append(task)
            task.func(*task.args, **task.kwargs)

    app.dependency_overrides[get_lm_studio_client] = lambda: mock_lm
    app.dependency_overrides[get_task_queue_worker] = lambda: _SyncTaskQueue()

    try:
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={"session_id": sid, "text": "Привет", "mode": "assistant"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(
                c.decode("utf-8", errors="ignore") if isinstance(c, bytes) else str(c)
                for c in resp.iter_raw()
            )
            assert "Ответ 1" in body

        assert len(recorded_tasks) == 1
        assert recorded_tasks[0].task_type == "llm"

        # Regenerate replaces last assistant message and also uses TaskQueue
        mock_lm.stream_chat_completion.return_value = ["Ответ 2"]
        with client.stream(
            "POST",
            "/api/chat/regenerate",
            json={"session_id": sid, "mode": "assistant"},
        ) as resp2:
            assert resp2.status_code == 200
            body2 = "".join(
                c.decode("utf-8", errors="ignore") if isinstance(c, bytes) else str(c)
                for c in resp2.iter_raw()
            )
            assert "Ответ 2" in body2

        assert len(recorded_tasks) == 2
        reloaded = SessionManager.load_session(sid)
        assert reloaded["messages"][-1]["content"] == "Ответ 2"
    finally:
        app.dependency_overrides.clear()


def test_art_direct_command_progress_and_scene_background(tmp_path, monkeypatch):
    hist_dir = tmp_path / "history"
    out_dir = tmp_path / "outputs"
    hist_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("services.session_manager.HISTORY_DIR", hist_dir)
    monkeypatch.setattr("services.session_manager.OUTPUTS_DIR", out_dir)
    monkeypatch.setattr("services.rp_cast_manager.HISTORY_DIR", hist_dir)
    monkeypatch.setattr("mobile_server.routes.art.OUTPUTS_DIR", out_dir)
    monkeypatch.setattr("mobile_server.services.art_service.OUTPUTS_DIR", out_dir)
    monkeypatch.setattr("services.memory_db.memory_db.db_path", tmp_path / "memory.db")
    monkeypatch.setattr(SessionManager, "_sessions_cache", {})
    monkeypatch.setattr(SessionManager, "_current_session", None)

    sess = SessionManager.create_session(mode="roleplay", title="Арт тест")
    sid = sess["id"]
    SessionManager.save_session(sess)

    fake_img = out_dir / "sessions" / sid / "gen_test.png"
    fake_img.parent.mkdir(parents=True, exist_ok=True)
    fake_img.write_bytes(b"png")

    mock_forge = MagicMock()
    mock_forge.text_to_image.return_value = fake_img
    mock_forge.get_progress.return_value = {
        "progress": 0.5,
        "step": 10,
        "total_steps": 20,
        "eta_relative": 3.0,
        "textinfo": "",
        "stage_name": "🎨 Создание общего плана",
        "percent": 50,
    }

    fake_plan = GenerationPlan(
        style="realistic",
        target_model_key="realistic",
        model_title="Realistic Vision",
        target_checkpoint="realistic.safetensors",
        positive_prompt="neon street",
        negative_prompt="bad quality",
        width=512,
        height=768,
        steps=24,
        cfg_scale=6.5,
        sampler_name="Euler a",
        scheduler="Automatic",
        enable_hr=False,
        hr_scale=1.0,
        hr_upscaler="Latent",
        denoising_strength=0.35,
        hr_second_pass_steps=10,
        clip_skip=1,
        enable_adetailer=False,
        seed=42,
    )
    mock_ad = MagicMock()
    mock_ad.build_direct_plan.return_value = fake_plan
    mock_ad.plan_from_visual_intent.return_value = fake_plan

    class _SyncTaskQueue:
        current_task = SimpleNamespace(task_type="image")

        def enqueue(self, task):
            task.func(*task.args, **task.kwargs)

    app.dependency_overrides[get_forge_client] = lambda: mock_forge
    app.dependency_overrides[get_art_director] = lambda: mock_ad
    app.dependency_overrides[get_task_queue_worker] = lambda: _SyncTaskQueue()

    try:
        client = TestClient(app)

        # Direct command delegates cleanly to build_direct_plan
        res_dir = client.post("/api/art/direct-command", json={"session_id": sid, "command": "!art neon street --steps 24"})
        assert res_dir.status_code == 200
        assert res_dir.json()["success"] is True
        mock_ad.build_direct_plan.assert_called_once_with(raw_prompt="!art neon street --steps 24")

        # Progress uses ForgeClient.get_progress
        res_prog = client.get("/api/art/progress")
        assert res_prog.status_code == 200
        assert res_prog.json()["step"] == 10
        assert res_prog.json()["total_steps"] == 20

        # Scene background aligns desktop & mobile cast/session fields
        res_bg = client.post("/api/art/scene-background", json={"session_id": sid, "prompt": "rainy alley"})
        assert res_bg.status_code == 200
        assert res_bg.json()["success"] is True

        cast_data = json.loads((hist_dir / f"cast_{sid}.json").read_text(encoding="utf-8"))
        assert cast_data["environment"]["location_dna"] == "rainy alley"
        reloaded_sess = SessionManager.load_session(sid)
        assert reloaded_sess["background_image"] == str(fake_img.resolve())

        # Gallery finds images
        res_gal = client.get(f"/api/art/gallery?session_id={sid}")
        assert res_gal.status_code == 200
        assert res_gal.json()["total"] >= 1
        assert res_gal.json()["items"][0]["session_id"] == sid
    finally:
        app.dependency_overrides.clear()


def test_shared_build_assistant_visual_intent_and_scene_background_plan():
    intent, is_self, flags = build_assistant_visual_intent(
        raw_prompt="1girl, studio --self --ar square --neg blurry",
        user_query="сделай селфи",
    )
    assert is_self is True
    assert intent.target_subject == TargetSubject.ELARIA
    assert intent.aspect_ratio == "square"
    assert intent.user_neg == "blurry"
    assert flags.is_self_portrait is True

    ad = ArtDirector()
    bg_plan = ad.build_scene_background_plan(
        location_prompt="neon street at night",
        preferred_style="realistic",
        use_visual_intent=True,
    )
    assert bg_plan.width == 768
    assert bg_plan.height == 512
    assert bg_plan.enable_adetailer is False
    assert "human" in bg_plan.negative_prompt.lower()


def test_qr_encoder_and_mobile_server_thread_lifecycle(qapp, monkeypatch):
    from mobile_server.api import (
        get_mobile_server_urls,
        start_mobile_server_thread,
        stop_mobile_server_thread,
        is_mobile_server_running,
    )
    from mobile_server.config import settings
    from ui.common.icon_utils import encode_qr_matrix, _mask_condition, _format_bits_m, render_qr_pixmap

    # 1. QR matrix round-trip check
    test_url = "http://192.168.1.42:8000"
    m = encode_qr_matrix(test_url)
    n = len(m)
    fmt = 0
    for i in range(15):
        b = m[8][14 - i] if i >= 9 else (m[8][7] if i == 8 else (m[8][8] if i == 7 else (m[7][8] if i == 6 else m[i][8])))
        if b:
            fmt |= (1 << i)
    mask = ((fmt ^ 0x5412) >> 10) & 0x07
    assert fmt == _format_bits_m(mask)
    pix = render_qr_pixmap(test_url)
    assert pix.width() > 100 and pix.width() == pix.height()

    # 2. Server thread start/stop on isolated test port
    monkeypatch.setattr(settings, "HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "PORT", 18022)
    urls = get_mobile_server_urls()
    assert any(u.endswith(":18022") for u in urls)

    ok, _ = start_mobile_server_thread(timeout=3.0)
    assert ok is True
    assert is_mobile_server_running() is True
    assert stop_mobile_server_thread(timeout=3.0) is True
    assert is_mobile_server_running() is False


def test_sidebar_replaces_audio_folder_with_mobile_server_button(qapp):
    from PyQt6.QtWidgets import QMainWindow
    from ui.components.sidebar_widget import SidebarWidget
    import ui.components.sidebar_widget as sw_mod
    import ui.controllers.dialog_controller as dc_mod

    # Old audio folder tails removed
    assert not hasattr(sw_mod, "AUDIO_DIR")
    assert not hasattr(dc_mod, "AUDIO_DIR")

    dummy_win = QMainWindow()
    dummy_win._open_campaign_info_dialog = lambda: None
    dummy_win._on_chat_list_item_clicked = lambda *a: None
    dummy_win._on_mobile_server_clicked = MagicMock()

    sb = SidebarWidget(dummy_win)
    assert hasattr(sb, "btn_mobile_server")
    assert sb.btn_mobile_server.toolTip() == "Запуск мобильного сервера"
    sb.btn_mobile_server.click()
    dummy_win._on_mobile_server_clicked.assert_called_once()

    sb.update_mobile_server_state(True)
    assert "запущен" in sb.btn_mobile_server.toolTip().lower()
    sb.update_mobile_server_state(False)
    assert sb.btn_mobile_server.toolTip() == "Запуск мобильного сервера"


def test_stream_art_tag_filter_suppresses_inline_tags_outside_code():
    from services.assistant_flow import StreamArtTagFilter

    f = StreamArtTagFilter()
    out1, trig1 = f.feed("Привет! [IMA")
    out2, trig2 = f.feed("GE_PROMPT: 1girl, neon --ar portrait]", prior_text=out1)
    out3, trig3 = f.flush(prior_text=out1 + out2)
    combined = out1 + out2 + out3
    assert "IMAGE_PROMPT" not in combined
    assert trig2 is True

    f2 = StreamArtTagFilter()
    code_sample = "Пример:\n```\n[IMAGE_PROMPT: 1boy, knight]\n```"
    c1, c_trig1 = f2.feed("Пример:\n```\n", prior_text="")
    c2, c_trig2 = f2.feed("[IMAGE_PROMPT: 1boy, knight]\n```", prior_text=c1)
    c3, _ = f2.flush(prior_text=c1 + c2)
    assert "[IMAGE_PROMPT: 1boy, knight]" in (c1 + c2 + c3)
    assert c_trig2 is False


def test_mobile_chat_stream_single_pass_assistant_and_rp_isolation(tmp_path, monkeypatch):
    hist_dir = tmp_path / "history"
    out_dir = tmp_path / "outputs"
    hist_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("services.session_manager.HISTORY_DIR", hist_dir)
    monkeypatch.setattr("services.session_manager.OUTPUTS_DIR", out_dir)
    monkeypatch.setattr("mobile_server.services.art_service.OUTPUTS_DIR", out_dir)
    monkeypatch.setattr("services.memory_db.memory_db.db_path", tmp_path / "memory.db")
    monkeypatch.setattr(SessionManager, "_sessions_cache", {})
    monkeypatch.setattr(SessionManager, "_current_session", None)

    sess_asst = SessionManager.create_session(mode="assistant", title="Арт Ассистент")
    sess_asst["last_generation"] = {"seed": 777123, "prompt": "prev", "checkpoint": "real.safetensors"}
    sid_asst = sess_asst["id"]
    SessionManager.save_session(sess_asst)

    sess_rp = SessionManager.create_session(mode="roleplay", title="РП Изоляция")
    sess_rp["character_data"] = {"name": "Лира", "visual_dna": "green eyes"}
    sid_rp = sess_rp["id"]
    SessionManager.save_session(sess_rp)

    fake_img = out_dir / "sessions" / sid_asst / "single_pass.png"
    fake_img.parent.mkdir(parents=True, exist_ok=True)
    fake_img.write_bytes(b"png")

    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True
    mock_forge.get_models.return_value = [{"title": "real.safetensors", "model_name": "real"}]
    mock_forge.text_to_image.return_value = fake_img
    mock_forge.last_generation_info = {"seed": 777123}

    fake_plan = GenerationPlan(
        style="realistic",
        target_model_key="realistic",
        model_title="Realistic",
        target_checkpoint="real.safetensors",
        positive_prompt="1girl, neon cafe",
        negative_prompt="bad quality",
        width=512,
        height=768,
        steps=20,
        cfg_scale=6.0,
        sampler_name="Euler a",
        scheduler="Automatic",
        enable_hr=False,
        hr_scale=1.0,
        hr_upscaler="Latent",
        denoising_strength=0.35,
        hr_second_pass_steps=10,
        clip_skip=1,
        enable_adetailer=False,
        seed=777123,
    )
    mock_ad = MagicMock()
    mock_ad.plan_from_visual_intent.return_value = fake_plan

    mock_lm = MagicMock()
    recorded_tasks = []

    class _SyncTaskQueue:
        current_task = None

        def enqueue(self, task):
            recorded_tasks.append(task)
            task.func(*task.args, **task.kwargs)

    app.dependency_overrides[get_lm_studio_client] = lambda: mock_lm
    app.dependency_overrides[get_forge_client] = lambda: mock_forge
    app.dependency_overrides[get_art_director] = lambda: mock_ad
    app.dependency_overrides[get_task_queue_worker] = lambda: _SyncTaskQueue()

    try:
        client = TestClient(app)

        # 1. Single-pass [IMAGE_PROMPT: ...] in Assistant triggers art_started, Seed Lock & VRAM cleanup
        mock_lm.stream_chat_completion.return_value = [
            "Секунду, рисую! ",
            "[IMAGE_PROMPT: 1girl, neon cafe ",
            "--ar portrait --model realistic]",
        ]
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={"session_id": sid_asst, "text": "Нарисуй такую же девушку в кафе", "mode": "assistant"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(
                c.decode("utf-8", errors="ignore") if isinstance(c, bytes) else str(c)
                for c in resp.iter_raw()
            )
            assert "art_started" in body
            assert "image_prompt" in body
            assert "[IMAGE_PROMPT:" not in body.split("event: done")[0]

        assert mock_forge.text_to_image.call_count == 1
        mock_forge.unload_checkpoint.assert_called_once()
        mock_forge.free_vram.assert_called_once()
        ad_intent = mock_ad.plan_from_visual_intent.call_args.kwargs["visual_intent"]
        assert ad_intent.seed == 777123
        assert mock_ad.plan_from_visual_intent.call_args.kwargs["available_models"] == [
            {"title": "real.safetensors", "model_name": "real"}
        ]
        reloaded_asst = SessionManager.load_session(sid_asst)
        assert reloaded_asst["messages"][-1]["image_url"].startswith("/media/image?path=")
        assert reloaded_asst["last_generation"]["seed"] == 777123

        # Verify GET /api/sessions/{sid} preserves image_url, image_path and generation_params
        detail_res = client.get(f"/api/sessions/{sid_asst}")
        assert detail_res.status_code == 200
        detail_last_msg = detail_res.json()["messages"][-1]
        assert detail_last_msg["image_url"].startswith("/media/image?path=")
        assert detail_last_msg["image_path"] == str(fake_img)
        assert detail_last_msg["generation_params"]["seed"] == 777123

        # Verify desktop-style message with only image_path synthesizes image_url
        reloaded_asst["messages"].append({
            "id": "desktop_only_img",
            "role": "assistant",
            "content": "",
            "image_path": str(fake_img),
        })
        SessionManager.save_session(reloaded_asst)
        detail_res2 = client.get(f"/api/sessions/{sid_asst}")
        assert detail_res2.status_code == 200
        assert detail_res2.json()["messages"][-1]["image_url"].startswith("/media/image?path=")

        # 2. RP Isolation: auto_generate_art=False stores COMPANION visual_intent without invoking Forge
        mock_forge.reset_mock()
        mock_ad.reset_mock()
        monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: {"auto_generate_art": False})
        mock_lm.stream_chat_completion.return_value = [
            "<stats>HP: 100</stats>\nЛира улыбнулась.\n",
            "[IMAGE_PROMPT: emerald eyes, tavern candlelight --ar portrait]",
        ]
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={"session_id": sid_rp, "text": "Оглядываюсь", "mode": "roleplay"},
        ) as resp_rp:
            assert resp_rp.status_code == 200
            body_rp = "".join(
                c.decode("utf-8", errors="ignore") if isinstance(c, bytes) else str(c)
                for c in resp_rp.iter_raw()
            )
            assert "art_started" not in body_rp
            assert "HP: 100" in body_rp

        mock_forge.text_to_image.assert_not_called()
        reloaded_rp = SessionManager.load_session(sid_rp)
        last_rp_msg = reloaded_rp["messages"][-1]
        assert "<stats>HP: 100</stats>" in last_rp_msg["content"]
        assert last_rp_msg["visual_intent"]["target_subject"] == TargetSubject.COMPANION.value
        assert last_rp_msg["visual_intent"]["mode"] == "roleplay"
    finally:
        app.dependency_overrides.clear()


def test_mobile_and_desktop_thought_cross_compatibility(qapp, tmp_path, monkeypatch):
    from ui.builders.chat_bubble_builder import ChatBubbleBuilderMixin
    from PyQt6.QtWidgets import QVBoxLayout, QWidget

    hist_dir = tmp_path / "history"
    out_dir = tmp_path / "outputs"
    hist_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("services.session_manager.HISTORY_DIR", hist_dir)
    monkeypatch.setattr("services.session_manager.OUTPUTS_DIR", out_dir)
    monkeypatch.setattr("services.memory_db.memory_db.db_path", tmp_path / "memory.db")
    monkeypatch.setattr(SessionManager, "_sessions_cache", {})
    monkeypatch.setattr(SessionManager, "_current_session", None)

    sess = SessionManager.create_session(mode="assistant", title="Мысли тест")
    sid = sess["id"]
    SessionManager.save_session(sess)

    mock_lm = MagicMock()
    mock_lm.stream_chat_completion.return_value = ["<think>Анализ вопроса</think>Готовый ответ"]

    class _SyncTaskQueue:
        current_task = None

        def enqueue(self, task):
            task.func(*task.args, **task.kwargs)

    app.dependency_overrides[get_lm_studio_client] = lambda: mock_lm
    app.dependency_overrides[get_task_queue_worker] = lambda: _SyncTaskQueue()

    try:
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={"session_id": sid, "text": "Думай", "mode": "assistant"},
        ) as resp:
            assert resp.status_code == 200
            _ = list(resp.iter_raw())

        reloaded = SessionManager.load_session(sid)
        saved_thought = reloaded["messages"][-1]["thought"]
        assert isinstance(saved_thought, dict)
        assert saved_thought["content"] == "Анализ вопроса"

        detail = client.get(f"/api/sessions/{sid}")
        assert detail.status_code == 200
        assert detail.json()["messages"][-1]["thought"] == "Анализ вопроса"

        # Desktop renderer must handle both canonical dict and legacy str thoughts without AttributeError
        class _DummyHost(QWidget, ChatBubbleBuilderMixin):
            def __init__(self):
                super().__init__()
                self.current_mode = "assistant"
                self.current_session = {"id": sid, "mode": "assistant"}
                self.chat_container = self
                self.chat_layout = QVBoxLayout(self)
                self.chat_layout.addStretch(1)
                self._enqueue_tts_task = lambda *a: None
                self._on_message_translate_requested = lambda *a: None
                self._on_message_regenerate_requested = lambda *a: None
                self._on_message_delete_requested = lambda *a: None
                self._on_scene_art_requested = lambda *a: None
                self._execute_workspace_script = lambda *a: None
                self._on_rpg_choice_selected = lambda *a: None

            def is_engineer_mode_active(self):
                return False

            def _get_current_avatar_path(self):
                return None

        host = _DummyHost()
        w1 = host._render_stored_message({"role": "assistant", "content": "ok1", "thought": saved_thought})
        w2 = host._render_stored_message({"role": "assistant", "content": "ok2", "thought": "legacy str thought"})
        assert w1 is not None and w2 is not None
    finally:
        app.dependency_overrides.clear()


def test_mobile_mode_override_does_not_clobber_assistant_skills_and_thinking_xtc():
    from mobile_server.services.chat_service import build_chat_context_and_params

    sess_asst = {"id": "mode_guard_1", "mode": "assistant", "chat_history": []}
    msgs, params, target_mode, _, _ = build_chat_context_and_params(
        session=sess_asst,
        session_id="mode_guard_1",
        user_text="нарисуй картинку кота",
        mode_override="roleplay",
        active_model_name="DeepSeek-R1-0528-Qwen3-8B-abliterated",
    )
    assert target_mode == AppMode.ASSISTANT
    assert "[SYSTEM DIRECTIVE:" in msgs[-1]["content"]
    assert params["xtc_probability"] == 0.0
    assert params["max_tokens"] == 4096
    assert params["enable_thinking"] is False
    assert params["temperature"] == pytest.approx(0.4)

    sess_rp = {"id": "mode_guard_2", "mode": "roleplay", "chat_history": []}
    _msgs_rp, _params_rp, target_mode_rp, _, _ = build_chat_context_and_params(
        session=sess_rp,
        session_id="mode_guard_2",
        user_text="нарисуй картинку кота",
        mode_override="assistant",
    )
    assert target_mode_rp == AppMode.ROLEPLAY


def test_mobile_build_chat_context_and_params_thinking_mode_matrix(monkeypatch):
    """
    Проверяет проброс thinking_mode и override для gallery в build_chat_context_and_params:
    1. thinking_mode == 'off' -> enable_thinking=False
    2. thinking_mode == 'on' -> enable_thinking=True
    3. thinking_mode == 'auto' -> True для engineer, False для assistant/roleplay
    4. explicit_override в sampling_overrides имеет приоритет
    """
    from mobile_server.services.chat_service import build_chat_context_and_params
    from utils.config_manager import ConfigManager

    # 1. 'off'
    monkeypatch.setattr(ConfigManager, "load_config", lambda: {
        "thinking_mode": "off",
        "assistant_model_name": "qwen3.5-35b"
    })
    _, params_off, _, _, _ = build_chat_context_and_params(
        session={"id": "s1", "mode": "assistant", "chat_history": []},
        session_id="s1",
        user_text="Привет, как дела?",
        active_model_name="qwen3.5-35b",
    )
    assert params_off["enable_thinking"] is False

    # 2. 'on'
    monkeypatch.setattr(ConfigManager, "load_config", lambda: {
        "thinking_mode": "on",
        "assistant_model_name": "qwen3.5-35b"
    })
    _, params_on, _, _, _ = build_chat_context_and_params(
        session={"id": "s2", "mode": "assistant", "chat_history": []},
        session_id="s2",
        user_text="Привет, как дела?",
        active_model_name="qwen3.5-35b",
    )
    assert params_on["enable_thinking"] is True

    # 3. 'auto': engineer -> True, assistant -> False
    monkeypatch.setattr(ConfigManager, "load_config", lambda: {
        "thinking_mode": "auto",
        "assistant_model_name": "qwen3.5-35b"
    })
    _, params_eng, _, _, _ = build_chat_context_and_params(
        session={"id": "s3", "mode": "engineer", "chat_history": []},
        session_id="s3",
        user_text="Напиши функцию на Python",
        active_model_name="qwen3.5-35b",
    )
    assert params_eng["enable_thinking"] is True

    _, params_asst, _, _, _ = build_chat_context_and_params(
        session={"id": "s4", "mode": "assistant", "chat_history": []},
        session_id="s4",
        user_text="Расскажи шутку",
        active_model_name="qwen3.5-35b",
    )
    assert params_asst["enable_thinking"] is False

    # 4. explicit_override
    _, params_ovr, _, _, _ = build_chat_context_and_params(
        session={"id": "s5", "mode": "assistant", "chat_history": []},
        session_id="s5",
        user_text="Привет",
        sampling_overrides={"enable_thinking": True},
        active_model_name="qwen3.5-35b",
    )
    assert params_ovr["enable_thinking"] is True


def test_art_progress_tracks_pending_queue_tasks():
    mock_forge = MagicMock()
    mock_forge.get_progress.return_value = {
        "progress": 0.0,
        "step": 0,
        "total_steps": 0,
        "eta_relative": 0.0,
        "textinfo": "",
        "stage_name": None,
        "percent": 0,
    }

    class _PendingQueue:
        current_task = None

        def has_pending_tasks(self, task_types):
            return "image" in task_types

    app.dependency_overrides[get_forge_client] = lambda: mock_forge
    app.dependency_overrides[get_task_queue_worker] = lambda: _PendingQueue()

    try:
        client = TestClient(app)
        res = client.get("/api/art/progress")
        assert res.status_code == 200
        body = res.json()
        assert body["is_generating"] is True
        assert "Ожидание" in (body["stage_name"] or "")
    finally:
        app.dependency_overrides.clear()


def test_mobile_shell_routes_no_cache_and_session_events_route():
    openapi_paths = set((app.openapi() or {}).get("paths", {}).keys())
    assert "/api/sessions/{session_id}/events" in openapi_paths

    client = TestClient(app)
    for path in ("/", "/static/sw.js", "/static/js/app.js", "/static/css/theme.css"):
        res = client.get(path)
        assert res.status_code == 200, f"Unexpected status for {path}: {res.status_code}"
        cc = res.headers.get("cache-control", "")
        assert "no-store" in cc and "no-cache" in cc, f"Missing no-cache header on {path}: {cc}"


def test_session_events_broker_art_completed_and_interrupted(tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from mobile_server.services import art_service, chat_service
    from services.session_manager import SessionManager

    fake_sess = SessionManager.create_session(mode="assistant", title="SSE Signal Test", append_greeting=False)
    sid = fake_sess["id"]
    msg_id = "msg-target-123"
    fake_sess["messages"] = [{"id": msg_id, "role": "assistant", "content": "Рисую..."}]
    SessionManager.save_session(fake_sess)

    monkeypatch.setattr(art_service, "OUTPUTS_DIR", tmp_path)
    out_img = tmp_path / "sessions" / sid / "img_ok.png"
    out_img.parent.mkdir(parents=True, exist_ok=True)
    out_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True

    dummy_plan = SimpleNamespace(
        positive_prompt="1girl, neon city",
        negative_prompt="bad anatomy",
        checkpoint="realistic_v1.safetensors",
        sampler="DPM++ 2M",
        steps=28,
        cfg_scale=6.5,
        width=768,
        height=1024,
        enable_hr=True,
        denoising_strength=0.35,
        seed=42,
    )
    monkeypatch.setattr(
        chat_service,
        "run_forge_art_generation",
        lambda **kwargs: {
            "image_path": str(out_img),
            "plan": dummy_plan,
            "forge_kwargs": {"seed": 42, "steps": 28},
            "last_meta": {"seed": 42},
        },
    )

    async def _collect_signals():
        gen = chat_service.subscribe_session_events(sid)
        first = await gen.__anext__()
        assert first["event"] == "connected"

        def _worker():
            chat_service._execute_and_persist_forge_task(
                session_id=sid,
                forge=mock_forge,
                target_msg_id=msg_id,
                mode="assistant",
            )

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        completed_ev = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
        t.join(timeout=2.0)

        def _interrupt_worker():
            chat_service.publish_session_event(sid, "art_interrupted", {"session_id": sid})

        t2 = threading.Thread(target=_interrupt_worker, daemon=True)
        t2.start()
        interrupted_ev = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
        t2.join(timeout=2.0)

        await gen.aclose()
        return completed_ev, interrupted_ev

    completed_ev, interrupted_ev = asyncio.run(_collect_signals())
    assert completed_ev["event"] == "art_completed"
    payload = json.loads(completed_ev["data"])
    assert payload["session_id"] == sid
    assert payload["message_id"] == msg_id
    assert payload["image_url"]
    assert payload["generation_params"]["seed"] == 42

    assert interrupted_ev["event"] == "art_interrupted"
    assert json.loads(interrupted_ev["data"])["session_id"] == sid

    try:
        mock_queue = MagicMock()
        mock_queue.current_task = SimpleNamespace(metadata={"session_id": sid})
        app.dependency_overrides[get_forge_client] = lambda: mock_forge
        app.dependency_overrides[get_task_queue_worker] = lambda: mock_queue
        client = TestClient(app)
        res = client.post("/api/art/interrupt")
        assert res.status_code == 200
        assert res.json()["interrupted"] is True
        mock_forge.interrupt.assert_called_once()
        mock_queue.abort_current.assert_called_once()
    finally:
        app.dependency_overrides.clear()
        SessionManager.delete_session(sid)
