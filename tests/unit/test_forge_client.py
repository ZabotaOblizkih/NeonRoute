"""
Unit tests for services/forge_client.py.
Covers:
- Helper functions: is_sdxl_checkpoint, has_intimate_or_dynamic_triggers, _detect_adetailer_needed
- Upscaler and safe hires settings resolution
- Model discovery, checkpoint switching, unloading, reloading and VRAM management
- Payload construction for txt2img, VAE tiling, FP8 configuration, and ADetailer injection
- Deserialization of base64 PNG responses, metadata extraction, and output file saving
- Fault tolerance: offline errors, watchdog timeouts, user cancellation, CUDA OOM fallbacks, empty response retries
- Progress polling loop and stage detection signals
100% offline, isolated from network and GPU.
"""

import base64
import io
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image

from services.forge_client import (
    ForgeClient,
    ForgeOfflineError,
    has_intimate_or_dynamic_triggers,
    is_sdxl_checkpoint,
    SDXL_MARKERS,
    STAGE_DETECTORS,
)


def _create_valid_png_b64(width: int = 64, height: int = 64) -> str:
    """Generates a valid base64-encoded PNG image with length >= 100 bytes."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="cyan").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@pytest.fixture(autouse=True)
def airgap_network_and_sleep(monkeypatch):
    """Guarantees 100% offline execution and sub-second speed by mocking network and sleep."""
    monkeypatch.setattr(time, "sleep", lambda s: None)

    # Safe default network responder
    def _default_resp(*args, **kwargs):
        r = MagicMock(spec=requests.Response)
        r.status_code = 200
        r.text = "{}"
        r.content = b"{}"
        r.json.return_value = {}
        return r

    monkeypatch.setattr("requests.get", _default_resp)
    monkeypatch.setattr("requests.post", _default_resp)
    monkeypatch.setattr("requests.Session.get", _default_resp)
    monkeypatch.setattr("requests.Session.post", _default_resp)


@pytest.fixture
def forge_client(tmp_path, monkeypatch):
    """Provides a fresh ForgeClient instance isolated from global singleton state."""
    ForgeClient._instance = None
    client = ForgeClient(base_url="http://127.0.0.1:7860")
    client._last_vram_wait_ts = time.time()
    monkeypatch.setattr("services.forge_client.OUTPUTS_DIR", tmp_path / "outputs")
    return client


# =========================================================================
# 1. Helper Functions Tests
# =========================================================================

class TestHelperFunctions:
    @pytest.mark.parametrize("model_name", ["", None, "   "])
    def test_is_sdxl_checkpoint_empty_or_none(self, model_name):
        assert is_sdxl_checkpoint(model_name) is False

    @pytest.mark.parametrize("marker", SDXL_MARKERS)
    def test_is_sdxl_checkpoint_markers(self, marker):
        assert is_sdxl_checkpoint(f"custom_{marker}_v1.safetensors") is True
        assert is_sdxl_checkpoint(f"MODEL_{marker.upper()}.ckpt") is True

    def test_is_sdxl_checkpoint_standard_sd15(self):
        assert is_sdxl_checkpoint("v1-5-pruned-emaonly.safetensors") is False
        assert is_sdxl_checkpoint("dreamshaper_8.safetensors") is False

    def test_is_sdxl_checkpoint_art_director_registry(self, monkeypatch):
        mock_models = [
            {"name": "ModelA", "checkpoint": "model_a.safetensors", "architecture": "sdxl"},
            {"name": "ModelB", "checkpoint": "model_b.safetensors", "base_model": "sdxl_base"},
            {"name": "ModelC", "checkpoint": "model_c.safetensors", "architecture": "pony"},
            {"name": "ModelD", "checkpoint": "model_d.safetensors", "size_gb": 6.5},
            {"name": "ModelE", "checkpoint": "model_e.safetensors", "size": 6.0},
            {"name": "ModelSmall", "checkpoint": "model_small.safetensors", "architecture": "sd15", "size_gb": 2.1},
        ]
        monkeypatch.setattr(
            "services.art_director_config.load_art_director_models",
            lambda: mock_models
        )
        assert is_sdxl_checkpoint("model_a.safetensors") is True
        assert is_sdxl_checkpoint("model_b.safetensors") is True
        assert is_sdxl_checkpoint("model_c.safetensors") is True
        assert is_sdxl_checkpoint("model_d.safetensors") is True
        assert is_sdxl_checkpoint("model_e.safetensors") is True
        assert is_sdxl_checkpoint("model_small.safetensors") is False
        assert is_sdxl_checkpoint("non_existent_model.safetensors") is False

    def test_is_sdxl_checkpoint_registry_exception(self, monkeypatch):
        def _raise():
            raise RuntimeError("Registry unavailable")
        monkeypatch.setattr("services.art_director_config.load_art_director_models", _raise)
        assert is_sdxl_checkpoint("unknown_model.safetensors") is False

    @pytest.mark.parametrize("text", ["", None])
    def test_has_intimate_or_dynamic_triggers_empty(self, text):
        assert has_intimate_or_dynamic_triggers(text) is False

    @pytest.mark.parametrize("trigger", [
        "oral", "blowjob", "blow job", "deepthroat", "kneeling",
        "on knees", "close-up interaction", "closeup interaction", "минет", "на коленях"
    ])
    def test_has_intimate_or_dynamic_triggers_detected(self, trigger):
        assert has_intimate_or_dynamic_triggers(f"masterpiece, high quality, {trigger}, sunset") is True
        assert has_intimate_or_dynamic_triggers(trigger.upper()) is True

    def test_has_intimate_or_dynamic_triggers_hyphen_variation(self):
        assert has_intimate_or_dynamic_triggers("portrait with blow-job angle") is True
        assert has_intimate_or_dynamic_triggers("standing on-knees") is True

    def test_has_intimate_or_dynamic_triggers_negative_case(self):
        assert has_intimate_or_dynamic_triggers("a valiant knight standing on a green hill") is False

    def test_detect_adetailer_needed_sdxl(self, forge_client):
        res = forge_client._detect_adetailer_needed("1girl in city", target_model_name="sdxl_base.safetensors")
        assert res is True

    def test_detect_adetailer_needed_intimate_trigger(self, forge_client):
        res = forge_client._detect_adetailer_needed("1girl, kneeling on floor", target_model_name="v1-5.safetensors")
        assert res is False

    def test_detect_adetailer_needed_anime_model(self, forge_client):
        res = forge_client._detect_adetailer_needed("1girl, smiling", target_model_name="meinahentai_v4.safetensors")
        assert res is False

    def test_detect_adetailer_needed_no_human_markers(self, forge_client):
        res = forge_client._detect_adetailer_needed("cyberpunk city landscape, neon lights, rainy street", target_model_name="v1-5.safetensors")
        assert res is False

    def test_detect_adetailer_needed_with_human_markers(self, forge_client):
        assert forge_client._detect_adetailer_needed("portrait of a warrior", target_model_name="v1-5.safetensors") is True
        assert forge_client._detect_adetailer_needed("1girl reading a book", target_model_name="v1-5.safetensors") is True
        assert forge_client._detect_adetailer_needed("портрет чародея в замке", target_model_name="v1-5.safetensors") is True


# =========================================================================
# 2. Client Initialization, Health & Connection Pre-flight
# =========================================================================

class TestForgeClientInitAndState:
    def test_forge_client_singleton_get_instance(self):
        ForgeClient._instance = None
        c1 = ForgeClient.get_instance()
        c2 = ForgeClient.get_instance()
        assert c1 is c2
        assert isinstance(c1, ForgeClient)

    def test_check_health_online(self, forge_client, monkeypatch):
        mock_resp = MagicMock(status_code=200)
        monkeypatch.setattr("requests.get", lambda *a, **k: mock_resp)
        ok, msg = forge_client.check_health()
        assert ok is True
        assert msg == "SD Forge Online"

    def test_check_health_http_error(self, forge_client, monkeypatch):
        mock_resp = MagicMock(status_code=502)
        monkeypatch.setattr("requests.get", lambda *a, **k: mock_resp)
        ok, msg = forge_client.check_health()
        assert ok is False
        assert "HTTP 502" in msg

    def test_check_health_connection_error(self, forge_client, monkeypatch):
        def _err(*a, **k):
            raise requests.exceptions.ConnectionError("Connection refused")
        monkeypatch.setattr("requests.get", _err)
        ok, msg = forge_client.check_health()
        assert ok is False
        assert msg == "SD Forge Offline"

    def test_check_health_generic_exception(self, forge_client, monkeypatch):
        def _err(*a, **k):
            raise TimeoutError("Connection timed out")
        monkeypatch.setattr("requests.get", _err)
        ok, msg = forge_client.check_health()
        assert ok is False
        assert "Offline (Connection timed out)" in msg

    def test_is_server_ready_socket_fails(self, forge_client, monkeypatch):
        monkeypatch.setattr("socket.create_connection", MagicMock(side_effect=ConnectionRefusedError))
        assert forge_client.is_server_ready() is False

    def test_is_server_ready_socket_ok_api_200(self, forge_client, monkeypatch):
        monkeypatch.setattr("socket.create_connection", MagicMock())
        monkeypatch.setattr("requests.get", lambda *a, **k: MagicMock(status_code=200))
        assert forge_client.is_server_ready() is True

    def test_is_server_ready_socket_ok_api_fails(self, forge_client, monkeypatch):
        monkeypatch.setattr("socket.create_connection", MagicMock())
        monkeypatch.setattr("requests.get", lambda *a, **k: MagicMock(status_code=500))
        assert forge_client.is_server_ready() is False

    def test_get_current_model_success(self, forge_client, monkeypatch):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"sd_model_checkpoint": "cyberrealistic_v4.safetensors"}
        monkeypatch.setattr("requests.get", lambda *a, **k: mock_resp)
        assert forge_client.get_current_model() == "cyberrealistic_v4.safetensors"

    def test_get_current_model_failure(self, forge_client, monkeypatch):
        def _err(*a, **k):
            raise Exception("API error")
        monkeypatch.setattr("requests.get", _err)
        assert forge_client.get_current_model() is None


# =========================================================================
# 3. Model Management & Checkpoint Switching
# =========================================================================

class TestModelsAndCheckpoints:
    def test_get_models_success(self, forge_client, monkeypatch):
        models_data = [{"title": "model_1", "model_name": "m1"}]
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = models_data
        monkeypatch.setattr("requests.get", lambda *a, **k: mock_resp)
        assert forge_client.get_models() == models_data

    def test_get_models_error(self, forge_client, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: MagicMock(status_code=500))
        assert forge_client.get_models() == []

    def test_get_installed_checkpoints_api(self, forge_client, monkeypatch):
        monkeypatch.setattr(forge_client, "is_running", lambda: True, raising=False)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = [
            {"filename": "C:/models/model_a.safetensors"},
            {"model_name": "model_b.ckpt"},
            {"title": "model_c [hash]"},
        ]
        monkeypatch.setattr("requests.get", lambda *a, **k: mock_resp)
        ckpts = forge_client.get_installed_checkpoints()
        assert "model_a.safetensors" in ckpts
        assert "model_b.ckpt" in ckpts
        assert "model_c [hash]" in ckpts

    def test_get_installed_checkpoints_fallback_disk(self, forge_client, tmp_path, monkeypatch):
        models_dir = tmp_path / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion"
        models_dir.mkdir(parents=True)
        (models_dir / "local_model.safetensors").write_text("dummy")
        (models_dir / "local_model_2.ckpt").write_text("dummy")
        (models_dir / "readme.txt").write_text("dummy")
        monkeypatch.setattr("services.forge_client.FORGE_ROOT_DIR", tmp_path)

        ckpts = forge_client.get_installed_checkpoints()
        assert "local_model.safetensors" in ckpts
        assert "local_model_2.ckpt" in ckpts
        assert "readme.txt" not in ckpts

    def test_refresh_checkpoints(self, forge_client, monkeypatch):
        monkeypatch.setattr("requests.post", lambda *a, **k: MagicMock(status_code=200))
        assert forge_client.refresh_checkpoints() is True
        monkeypatch.setattr("requests.post", lambda *a, **k: MagicMock(status_code=500))
        assert forge_client.refresh_checkpoints() is False
        def _err(*a, **k):
            raise Exception("timeout")
        monkeypatch.setattr("requests.post", _err)
        assert forge_client.refresh_checkpoints() is False

    def test_set_model_empty(self, forge_client):
        assert forge_client.set_model("") is False

    def test_set_model_already_active(self, forge_client, monkeypatch):
        monkeypatch.setattr(forge_client, "get_models", lambda: [{"title": "cyberrealistic_v4.safetensors"}])
        monkeypatch.setattr(forge_client, "get_current_model", lambda: "cyberrealistic_v4.safetensors")
        post_mock = MagicMock()
        monkeypatch.setattr("requests.post", post_mock)
        res = forge_client.set_model("cyberrealistic_v4.safetensors")
        assert res is True
        post_mock.assert_not_called()

    def test_set_model_sdxl_uses_fp8e4(self, forge_client, monkeypatch):
        monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: {"forge_sdxl_fp8_enabled": True})
        monkeypatch.setattr(forge_client, "get_models", lambda: [{"title": "sdxl_base.safetensors"}])
        monkeypatch.setattr(forge_client, "get_current_model", lambda: "sd15.safetensors")
        post_mock = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setattr("requests.post", post_mock)

        res = forge_client.set_model("sdxl_base.safetensors")
        assert res is True
        call_json = post_mock.call_args[1]["json"]
        assert call_json["sd_model_checkpoint"] == "sdxl_base.safetensors"
        assert call_json["forge_unet_storage_dtype"] == "fp8e4"

    def test_set_model_sd15_uses_auto(self, forge_client, monkeypatch):
        monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: {"forge_sdxl_fp8_enabled": True})
        monkeypatch.setattr(forge_client, "get_models", lambda: [{"title": "v1-5-pruned.safetensors"}])
        monkeypatch.setattr(forge_client, "get_current_model", lambda: "old.safetensors")
        post_mock = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setattr("requests.post", post_mock)

        res = forge_client.set_model("v1-5-pruned.safetensors")
        assert res is True
        call_json = post_mock.call_args[1]["json"]
        assert call_json["forge_unet_storage_dtype"] == "Auto"

    def test_set_model_http_error(self, forge_client, monkeypatch):
        monkeypatch.setattr(forge_client, "get_models", lambda: [])
        monkeypatch.setattr(forge_client, "get_current_model", lambda: None)
        monkeypatch.setattr("requests.post", lambda *a, **k: MagicMock(status_code=500, text="Internal Error"))
        assert forge_client.set_model("model.safetensors") is False


# =========================================================================
# 4. VRAM Management & Silent Process Launch
# =========================================================================

class TestVRAMManagement:
    def test_wait_for_vram_clear_throttled(self, forge_client):
        forge_client._last_vram_wait_ts = time.time()
        mock_monitor = MagicMock()
        forge_client.system_monitor = mock_monitor
        forge_client._wait_for_vram_clear()
        mock_monitor.get_free_vram_mb.assert_not_called()

    def test_wait_for_vram_clear_with_monitor(self, forge_client):
        forge_client._last_vram_wait_ts = 0.0
        mock_monitor = MagicMock()
        mock_monitor.get_free_vram_mb.side_effect = [2000, 4000]
        forge_client.system_monitor = mock_monitor
        forge_client._wait_for_vram_clear()
        assert mock_monitor.get_free_vram_mb.call_count == 2
        assert forge_client._last_vram_wait_ts > 0

    def test_wait_for_vram_clear_without_monitor(self, forge_client):
        forge_client._last_vram_wait_ts = 0.0
        forge_client.system_monitor = None
        forge_client._wait_for_vram_clear()
        assert forge_client._last_vram_wait_ts > 0

    def test_unload_checkpoint_when_already_unloaded(self, forge_client, monkeypatch):
        forge_client._checkpoint_unloaded = True
        post_mock = MagicMock()
        monkeypatch.setattr("requests.post", post_mock)
        assert forge_client.unload_checkpoint() is True
        post_mock.assert_not_called()

    def test_unload_checkpoint_api_flow(self, forge_client, monkeypatch):
        forge_client._checkpoint_unloaded = False
        post_mock = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setattr("requests.post", post_mock)
        assert forge_client.unload_checkpoint() is True
        assert forge_client._checkpoint_unloaded is True
        endpoints = [c[0][0] for c in post_mock.call_args_list]
        assert any("unload-checkpoint" in ep for ep in endpoints)
        assert any("free-vram" in ep for ep in endpoints)

    def test_free_vram_payload_parameters(self, forge_client, monkeypatch):
        forge_client._checkpoint_unloaded = False
        post_mock = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setattr("requests.post", post_mock)
        res = forge_client.free_vram(always_offload=True, inference_memory_mb=2048, unload_models=False)
        assert res is True
        call_json = post_mock.call_args[1]["json"]
        assert call_json == {
            "unload_models": False,
            "always_offload": True,
            "inference_memory_mb": 2048
        }

    def test_reload_checkpoint_success(self, forge_client, monkeypatch):
        monkeypatch.setattr("requests.post", lambda *a, **k: MagicMock(status_code=200))
        mock_set = MagicMock(return_value=True)
        monkeypatch.setattr(forge_client, "set_model", mock_set)
        forge_client._checkpoint_unloaded = True
        assert forge_client.reload_checkpoint(checkpoint_name="target.safetensors") is True
        assert forge_client._checkpoint_unloaded is False
        mock_set.assert_called_once_with("target.safetensors")

    def test_interrupt_flow(self, forge_client, monkeypatch):
        mock_session = MagicMock()
        forge_client._current_session = mock_session
        post_mock = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setattr("requests.post", post_mock)
        forge_client.interrupt()
        assert forge_client._is_aborted is True
        mock_session.close.assert_called_once()

    def test_launch_forge_process_silently_missing_file(self, tmp_path):
        res = ForgeClient.launch_forge_process_silently(launcher_path=tmp_path / "non_existent.bat")
        assert res is None

    def test_launch_forge_process_silently_launches_subprocess(self, tmp_path, monkeypatch):
        launcher = tmp_path / "run_forge.bat"
        launcher.write_text("@echo off")
        monkeypatch.setattr("services.art_director.vram_policy.resolve_forge_offload_policy",
                            lambda **kw: {"always_offload": True})
        monkeypatch.setattr("config.FORGE_EXTRA_ARGS", ["--opt-sdp-attention", "--xformers"])
        monkeypatch.setattr("services.forge_client.get_clean_forge_env", lambda: {"TEST": "1"})

        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        res = ForgeClient.launch_forge_process_silently(launcher_path=launcher)
        assert res is not None
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "cmd.exe" in cmd[0]
        assert "--always-offload-from-vram" in cmd
        assert "--xformers" in cmd


# =========================================================================
# 5. Upscalers, Embeddings & Hires.fix Resolution
# =========================================================================

class TestUpscalersAndEmbeddings:
    def test_get_installed_embeddings_api_and_disk(self, forge_client, tmp_path, monkeypatch):
        emb_dir = tmp_path / "webui_forge_cu121_torch231" / "webui" / "embeddings"
        emb_dir.mkdir(parents=True)
        (emb_dir / "easynegative.safetensors").write_text("data")
        monkeypatch.setattr("services.forge_client.FORGE_ROOT_DIR", tmp_path)

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"loaded": {"badhandv4": {}}}
        monkeypatch.setattr("requests.get", lambda *a, **k: mock_resp)

        embeddings = forge_client.get_installed_embeddings()
        assert "badhandv4" in embeddings
        assert "easynegative" in embeddings
        assert forge_client.get_installed_embeddings() is embeddings

    def test_get_upscalers_success_and_cache(self, forge_client, monkeypatch):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = [{"name": "R-ESRGAN 4x+"}, {"name": "Latent"}, {"name": "ScuNET"}]
        monkeypatch.setattr("requests.get", lambda *a, **k: mock_resp)

        upscalers = forge_client.get_upscalers()
        assert upscalers == ["R-ESRGAN 4x+", "Latent", "ScuNET"]
        assert forge_client.get_upscalers() is upscalers

    def test_resolve_safe_hires_settings_exact_match(self, forge_client):
        forge_client._cached_upscalers = ["R-ESRGAN 4x+", "Latent"]
        upscaler, denoise = forge_client.resolve_safe_hires_settings("R-ESRGAN 4x+", requested_denoising=0.35)
        assert upscaler == "R-ESRGAN 4x+"
        assert denoise == 0.35

    def test_resolve_safe_hires_settings_anime_match(self, forge_client):
        forge_client._cached_upscalers = ["R-ESRGAN 4x+ Anime6B", "Latent"]
        upscaler, denoise = forge_client.resolve_safe_hires_settings("Anime upscaler", requested_denoising=0.40)
        assert upscaler == "R-ESRGAN 4x+ Anime6B"
        assert denoise == 0.40

    def test_resolve_safe_hires_settings_latent_fallback_boosts_low_denoise(self, forge_client):
        forge_client._cached_upscalers = ["Latent"]
        upscaler, denoise = forge_client.resolve_safe_hires_settings("NonExistentUpscaler", requested_denoising=0.30)
        assert upscaler == "Latent"
        assert denoise == 0.50

    def test_resolve_safe_hires_settings_latent_fallback_preserves_high_denoise(self, forge_client):
        forge_client._cached_upscalers = ["Latent"]
        upscaler, denoise = forge_client.resolve_safe_hires_settings("Latent", requested_denoising=0.60)
        assert upscaler == "Latent"
        assert denoise == 0.60


# =========================================================================
# 6. text_to_image / txt2img Generation & Fault Tolerance
# =========================================================================

class TestTextToImageGeneration:
    @pytest.fixture(autouse=True)
    def mock_poller_thread(self, monkeypatch):
        """Mocks threading.Thread to provide a fast dummy thread with instant start/join."""
        def dummy_thread(*args, **kwargs):
            t = MagicMock()
            t.start = MagicMock()
            t.join = MagicMock()
            return t
        monkeypatch.setattr("services.forge_client.threading.Thread", dummy_thread)

    @staticmethod
    def _patch_session_post(monkeypatch, handler):
        def _wrapper(self, url, *args, **kwargs):
            return handler(url, *args, **kwargs)
        monkeypatch.setattr("requests.Session.post", _wrapper)

    def test_txt2img_offline_preflight_raises_forge_offline_error(self, forge_client, monkeypatch):
        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: False)
        with pytest.raises(ForgeOfflineError) as exc_info:
            forge_client.text_to_image(prompt="test prompt")
        assert "SD WebUI Forge не запущен" in str(exc_info.value)

    def test_txt2img_successful_flow(self, forge_client, tmp_path, monkeypatch):
        img_b64 = _create_valid_png_b64(128, 128)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "images": [img_b64],
            "info": json.dumps({"seed": 424242, "prompt": "1girl sitting by the window"}),
        }

        captured_payloads = []

        def mock_post(url, *args, **kwargs):
            if "txt2img" in url:
                captured_payloads.append(kwargs.get("json", {}))
                return mock_resp
            return MagicMock(status_code=200)

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        monkeypatch.setattr(forge_client, "set_model", lambda ckpt: True)
        self._patch_session_post(monkeypatch, mock_post)
        mock_unload = MagicMock(return_value=True)
        monkeypatch.setattr(forge_client, "unload_checkpoint", mock_unload)

        output_file = forge_client.text_to_image(
            prompt="  a cybernetic warrior \n\n with glowing eyes\t",
            negative_prompt="blurry,\n ugly",
            width=500,  # Round to 448
            height=520,  # Round to 512
            steps=25,
            cfg_scale=6.5,
            seed=424242,
            checkpoint="dreamshaper.safetensors",
            enable_hr=True,
            hr_scale=1.5,
            output_dir=tmp_path / "custom_out"
        )

        assert output_file.exists()
        assert output_file.suffix == ".png"
        assert forge_client.last_generation_info.get("seed") == 424242
        mock_unload.assert_called_once()

        assert len(captured_payloads) == 1
        p = captured_payloads[0]
        assert p["prompt"] == "a cybernetic warrior with glowing eyes"
        assert p["negative_prompt"] == "blurry, ugly"
        assert p["width"] == 448
        assert p["height"] == 512
        assert p["steps"] == 25
        assert p["cfg_scale"] == 6.5
        assert p["enable_hr"] is True
        assert p["hr_scale"] == 1.5

    def test_txt2img_clip_skip2_for_urpm_model(self, forge_client, monkeypatch):
        img_b64 = _create_valid_png_b64(64, 64)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"images": [img_b64], "info": "{}"}

        captured_payloads = []

        def mock_post(url, *args, **kwargs):
            if "txt2img" in url:
                captured_payloads.append(kwargs.get("json", {}))
                return mock_resp
            return MagicMock(status_code=200)

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        monkeypatch.setattr(forge_client, "set_model", lambda ckpt: True)
        self._patch_session_post(monkeypatch, mock_post)

        forge_client.text_to_image(
            prompt="test",
            checkpoint="urpm_merge_v1.safetensors",
            clip_skip=1
        )
        assert captured_payloads[0]["clip_skip"] == 2

    def test_txt2img_session_id_directory_structure(self, forge_client, tmp_path, monkeypatch):
        monkeypatch.setattr("services.forge_client.OUTPUTS_DIR", tmp_path / "global_outputs")
        img_b64 = _create_valid_png_b64(64, 64)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"images": [f"data:image/png;base64,{img_b64}"], "info": "{}"}

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        monkeypatch.setattr(forge_client, "set_model", lambda ckpt: True)
        self._patch_session_post(monkeypatch, lambda url, *a, **k: mock_resp)

        out_path = forge_client.text_to_image(prompt="test", session_id="sess_123")
        assert "sessions" in str(out_path)
        assert "sess_123" in str(out_path)
        assert out_path.exists()

    def test_txt2img_user_abort_raises_interrupted_error(self, forge_client, monkeypatch):
        def mock_abort_post(url, *a, **k):
            forge_client.interrupt()
            return MagicMock(status_code=200)

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, mock_abort_post)

        with pytest.raises(InterruptedError) as exc_info:
            forge_client.text_to_image(prompt="test")
        assert "отменена пользователем" in str(exc_info.value)

    def test_txt2img_watchdog_timeout_raises_timeout_error(self, forge_client, monkeypatch):
        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        def _throw(*a, **k):
            raise Exception("Stalled")
        self._patch_session_post(monkeypatch, _throw)
        monkeypatch.setattr("threading.Event.is_set", lambda self: True)

        with pytest.raises(TimeoutError) as exc_info:
            forge_client.text_to_image(prompt="test")
        assert "Watchdog reset" in str(exc_info.value)

    def test_txt2img_fallback_tier1_retry_without_adetailer(self, forge_client, monkeypatch):
        img_b64 = _create_valid_png_b64(64, 64)
        fail_resp = MagicMock(status_code=500, text="ADetailer detector error")
        success_resp = MagicMock(status_code=200)
        success_resp.json.return_value = {"images": [img_b64], "info": "{}"}

        post_calls = []

        def mock_post(url, *args, **kwargs):
            if "txt2img" in url:
                payload = json.loads(json.dumps(kwargs.get("json", {})))
                post_calls.append(payload)
                if len(post_calls) == 1:
                    return fail_resp
                return success_resp
            return MagicMock(status_code=200)

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, mock_post)

        out = forge_client.text_to_image(
            prompt="test",
            alwayson_scripts={"ADetailer": {"args": [True]}}
        )
        assert out.exists()
        assert len(post_calls) == 2
        assert "alwayson_scripts" in post_calls[0]
        assert "alwayson_scripts" not in post_calls[1]

    def test_txt2img_fallback_tier2_cuda_oom_retries_without_hires(self, forge_client, monkeypatch):
        img_b64 = _create_valid_png_b64(64, 64)
        oom_resp = MagicMock(status_code=500, text="torch.cuda.OutOfMemoryError: CUDA out of memory")
        success_resp = MagicMock(status_code=200)
        success_resp.json.return_value = {"images": [img_b64], "info": "{}"}

        post_calls = []

        def mock_post(url, *args, **kwargs):
            if "txt2img" in url:
                payload = json.loads(json.dumps(kwargs.get("json", {})))
                post_calls.append(payload)
                if len(post_calls) == 1:
                    return oom_resp
                return success_resp
            return MagicMock(status_code=200)

        mock_free = MagicMock(return_value=True)
        monkeypatch.setattr(forge_client, "free_vram", mock_free)
        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, mock_post)

        out = forge_client.text_to_image(prompt="test", enable_hr=True)
        assert out.exists()
        assert len(post_calls) == 2
        assert post_calls[0]["enable_hr"] is True
        assert post_calls[1]["enable_hr"] is False
        mock_free.assert_any_call(always_offload=True, inference_memory_mb=1024, unload_models=True)

    def test_txt2img_empty_images_with_hires_retries_without_hires(self, forge_client, monkeypatch):
        img_b64 = _create_valid_png_b64(64, 64)
        empty_resp = MagicMock(status_code=200)
        empty_resp.json.return_value = {"images": [], "info": "{}"}
        success_resp = MagicMock(status_code=200)
        success_resp.json.return_value = {"images": [img_b64], "info": "{}"}

        post_calls = []

        def mock_post(url, *args, **kwargs):
            if "txt2img" in url:
                post_calls.append(kwargs.get("json", {}))
                if len(post_calls) == 1:
                    return empty_resp
                return success_resp
            return MagicMock(status_code=200)

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, mock_post)

        out = forge_client.text_to_image(prompt="test", enable_hr=True)
        assert out.exists()
        assert len(post_calls) == 2
        assert post_calls[1]["enable_hr"] is False

    def test_txt2img_empty_images_raises_value_error(self, forge_client, monkeypatch):
        empty_resp = MagicMock(status_code=200)
        empty_resp.json.return_value = {"images": [], "info": "{}"}
        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, lambda url, *a, **k: empty_resp)

        with pytest.raises(ValueError) as exc:
            forge_client.text_to_image(prompt="test", enable_hr=False)
        assert "пустой список изображений" in str(exc.value)

    def test_txt2img_corrupt_bytes_raises_value_error(self, forge_client, monkeypatch):
        short_b64 = base64.b64encode(b"short").decode("utf-8")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"images": [short_b64], "info": "{}"}
        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, lambda url, *a, **k: resp)

        with pytest.raises(ValueError) as exc:
            forge_client.text_to_image(prompt="test")
        assert "поврежденные или неполные байты" in str(exc.value)

    def test_txt2img_connection_error_raises_forge_offline_error(self, forge_client, monkeypatch):
        def _err(*a, **k):
            raise requests.exceptions.ConnectionError("offline")
        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, _err)

        with pytest.raises(ForgeOfflineError) as exc:
            forge_client.text_to_image(prompt="test")
        assert "SD WebUI Forge не запущен" in str(exc.value)

    def test_progress_poller_stage_detection_signals(self, forge_client):
        """Simulates progress poller loop execution and checks emitted stage labels."""
        emitted_signals = []
        forge_client.generation_progress.connect(lambda s, p: emitted_signals.append((s, p)))

        samples = [
            {"progress": 0.2, "state": {"sampling_step": 5, "sampling_steps": 25, "textinfo": "hand_yolov8 step"}},
            {"progress": 0.5, "state": {"sampling_step": 12, "sampling_steps": 25, "textinfo": "face_mesh_eyes step"}},
            {"progress": 0.8, "state": {"sampling_step": 20, "sampling_steps": 25, "textinfo": ""}},
        ]

        last_stage = ""
        last_pct = -1

        for data in samples:
            state = data.get("state", {})
            textinfo = str(state.get("textinfo") or "").lower()
            step = state.get("sampling_step", 0)
            total_steps = state.get("sampling_steps", 0)
            progress_float = data.get("progress", 0.0)

            stage_name = None
            for keyword, title in STAGE_DETECTORS:
                if keyword in textinfo:
                    stage_name = title
                    break

            if not stage_name:
                stage_name = "🎨 Создание общего плана"

            pct = int((step / total_steps) * 100) if total_steps > 0 else int(progress_float * 100)
            pct = max(0, min(100, pct))

            if stage_name != last_stage or abs(pct - last_pct) >= 2:
                last_stage = stage_name
                last_pct = pct
                forge_client.generation_progress.emit(stage_name, pct)

        assert any("✋ Улучшение рук" in s[0] for s in emitted_signals)
        assert any("👁️ Детализация взгляда" in s[0] for s in emitted_signals)
        assert any("🎨 Создание общего плана" in s[0] for s in emitted_signals)

    def test_txt2img_with_vae_tiling_and_slots_summary(self, forge_client, monkeypatch):
        monkeypatch.setattr("services.forge_client.FORGE_ENABLE_VAE_TILING", True)
        img_b64 = _create_valid_png_b64(64, 64)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "images": [img_b64],
            "info": {"seed": 100}  # dict directly
        }

        captured = []
        def mock_post(url, *a, **kwargs):
            if "txt2img" in url:
                captured.append(kwargs.get("json", {}))
                return mock_resp
            return MagicMock(status_code=200)

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, mock_post)

        forge_client.text_to_image(
            prompt="test",
            checkpoint=None,
            alwayson_scripts={
                "ADetailer": {
                    "args": [
                        True, False,
                        {"ad_model": "face_yolov8n", "ad_denoising_strength": 0.35, "ad_inpaint_width": 512, "ad_steps": 20}
                    ]
                }
            }
        )
        assert len(captured) == 1
        assert captured[0].get("forge_vae_tiling") is True
        assert captured[0].get("override_settings", {}).get("sd_vae_tile") is True

    def test_txt2img_invalid_info_json_fallback(self, forge_client, monkeypatch):
        img_b64 = _create_valid_png_b64(64, 64)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"images": [img_b64], "info": "{invalid_json"}

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        self._patch_session_post(monkeypatch, lambda url, *a, **k: mock_resp)

        out = forge_client.text_to_image(prompt="test")
        assert out.exists()
        assert forge_client.last_generation_info == {}

    def test_txt2img_cached_title_and_pil_image_exception(self, forge_client, monkeypatch):
        forge_client._last_resolved_title = "cyberrealistic_v4.safetensors"
        img_b64 = _create_valid_png_b64(64, 64)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"images": [img_b64], "info": json.dumps({"seed": -1})}

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        monkeypatch.setattr("PIL.Image.open", MagicMock(side_effect=Exception("corrupt PIL")))
        self._patch_session_post(monkeypatch, lambda url, *a, **k: mock_resp)

        out = forge_client.text_to_image(prompt="test", checkpoint="cyberrealistic_v4.safetensors", seed=999, enable_hr=True, hr_scale=1.5)
        assert out.exists()
        assert forge_client.last_generation_info.get("seed") == 999

    def test_error_branches_and_edge_cases(self, forge_client, monkeypatch):
        # get_installed_embeddings exception in disk search
        forge_client._cached_embeddings = None
        monkeypatch.setattr("requests.get", MagicMock(side_effect=Exception("emb error")))
        monkeypatch.setattr("services.forge_client.FORGE_ROOT_DIR", Path("Z:/NonExistentPath/123"))
        assert isinstance(forge_client.get_installed_embeddings(), list)

        # get_upscalers exception
        forge_client._cached_upscalers = None
        monkeypatch.setattr("requests.get", MagicMock(side_effect=Exception("upscaler error")))
        assert forge_client.get_upscalers() == []

        # resolve_safe_hires_settings fallback to r-esrgan 4x+
        forge_client._cached_upscalers = ["r-esrgan 4x+"]
        up, den = forge_client.resolve_safe_hires_settings(None)
        assert up == "r-esrgan 4x+"

        # set_model realistic cyber filter branch
        forge_client._last_resolved_title = None
        monkeypatch.setattr(forge_client, "get_models", lambda: [{"title": "cyberrealistic_v1", "model_name": "cyber", "filename": "cyber.safetensors"}])
        monkeypatch.setattr(forge_client, "get_current_model", lambda: None)
        monkeypatch.setattr("requests.post", lambda *a, **k: MagicMock(status_code=200))
        assert forge_client.set_model("realistic") is True

        # set_model generic exception
        monkeypatch.setattr(forge_client, "get_models", MagicMock(side_effect=RuntimeError("err")))
        assert forge_client.set_model("any") is False

        # unload_checkpoint status_code != 200 and exception
        forge_client._checkpoint_unloaded = False
        monkeypatch.setattr("requests.post", lambda *a, **k: MagicMock(status_code=500, text="fail"))
        assert forge_client.unload_checkpoint() is True

        forge_client._checkpoint_unloaded = False
        monkeypatch.setattr("requests.post", MagicMock(side_effect=Exception("unload fail")))
        assert forge_client.unload_checkpoint() is True

        # free_vram short-circuit
        forge_client._checkpoint_unloaded = True
        assert forge_client.free_vram(always_offload=None, inference_memory_mb=None, unload_models=True) is True

        # free_vram exception
        forge_client._checkpoint_unloaded = False
        monkeypatch.setattr("requests.post", MagicMock(side_effect=Exception("free fail")))
        assert forge_client.free_vram(always_offload=True) is False

        # reload_checkpoint failure status and exception
        monkeypatch.setattr("requests.post", lambda *a, **k: MagicMock(status_code=500, text="fail"))
        assert forge_client.reload_checkpoint() is False

        monkeypatch.setattr("requests.post", MagicMock(side_effect=Exception("reload fail")))
        assert forge_client.reload_checkpoint() is False

        # interrupt exception branches
        monkeypatch.setattr("requests.post", MagicMock(side_effect=Exception("interrupt fail")))
        mock_sess = MagicMock()
        mock_sess.close.side_effect = Exception("close fail")
        forge_client._current_session = mock_sess
        forge_client.interrupt()  # Should not raise exception

