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

    def test_set_options_success_and_failure(self, forge_client, monkeypatch):
        """Проверка отправки параметров через POST /sdapi/v1/options."""
        # 1. HTTP 200 -> True
        post_mock = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setattr("requests.post", post_mock)
        ok = forge_client.set_options({"sd_checkpoint_cache": 0})
        assert ok is True
        post_mock.assert_called_once()
        assert post_mock.call_args[1]["json"] == {"sd_checkpoint_cache": 0}

        # 2. HTTP 500 -> False
        monkeypatch.setattr("requests.post", lambda *a, **k: MagicMock(status_code=500, text="Error"))
        assert forge_client.set_options({"sd_checkpoint_cache": 0}) is False

        # 3. ConnectionError -> False
        def raise_conn(*a, **k):
            raise requests.exceptions.ConnectionError("Connection refused")
        monkeypatch.setattr("requests.post", raise_conn)
        assert forge_client.set_options({"sd_checkpoint_cache": 0}) is False

    def test_set_model_on_16gb_system_forces_sd_checkpoint_cache_zero(self, forge_client, monkeypatch):
        """На системах <= 16.5 ГБ RAM set_model явно передает sd_checkpoint_cache=0 и sd_checkpoints_limit=1."""
        fake_vm = MagicMock(total=15.8 * (1024 ** 3))
        monkeypatch.setattr("psutil.virtual_memory", lambda: fake_vm)
        monkeypatch.setattr(forge_client, "get_models", lambda: [{"title": "test_ckpt.safetensors"}])
        monkeypatch.setattr(forge_client, "get_current_model", lambda: "other.safetensors")
        post_mock = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setattr("requests.post", post_mock)

        res = forge_client.set_model("test_ckpt.safetensors")
        assert res is True
        call_json = post_mock.call_args[1]["json"]
        assert call_json["sd_checkpoint_cache"] == 0
        assert call_json["sd_checkpoints_limit"] == 1
        assert call_json["sd_checkpoints_keep_in_cpu"] is False

    def test_ensure_system_options_sets_cache_zero_on_16gb_and_skips_on_32gb(self, forge_client, monkeypatch):
        """ensure_system_options отправляет настройки только при RAM <= 16.5 ГБ."""
        # 1. 16 ГБ RAM -> вызов set_options
        fake_vm_16 = MagicMock(total=16.0 * (1024 ** 3))
        monkeypatch.setattr("psutil.virtual_memory", lambda: fake_vm_16)
        mock_set_opts = MagicMock(return_value=True)
        monkeypatch.setattr(forge_client, "set_options", mock_set_opts)
        assert forge_client.ensure_system_options() is True
        mock_set_opts.assert_called_once_with({
            "sd_checkpoint_cache": 0,
            "sd_checkpoints_limit": 1,
            "sd_checkpoints_keep_in_cpu": False,
        })

        # 2. 32 ГБ RAM -> пропуск вызова
        fake_vm_32 = MagicMock(total=32.0 * (1024 ** 3))
        monkeypatch.setattr("psutil.virtual_memory", lambda: fake_vm_32)
        mock_set_opts.reset_mock()
        assert forge_client.ensure_system_options() is True
        mock_set_opts.assert_not_called()


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
        unload_calls = [c for c in post_mock.call_args_list if "unload-checkpoint" in c[0][0]]
        assert len(unload_calls) == 1
        assert unload_calls[0][1]["timeout"] >= 15.0
        free_calls = [c for c in post_mock.call_args_list if "free-vram" in c[0][0]]
        assert len(free_calls) == 1
        assert free_calls[0][1]["timeout"] >= 10.0

    def test_unload_checkpoint_read_timeout_waits_and_serializes(self, forge_client, monkeypatch):
        forge_client._checkpoint_unloaded = False
        sleep_mock = MagicMock()
        monkeypatch.setattr("time.sleep", sleep_mock)

        def mock_post(url, *args, **kwargs):
            if "unload-checkpoint" in url:
                raise requests.exceptions.ReadTimeout("Timeout moving SDXL to RAM")
            return MagicMock(status_code=200)

        monkeypatch.setattr("requests.post", mock_post)
        res = forge_client.unload_checkpoint()
        assert res is False
        assert forge_client._checkpoint_unloaded is False
        sleep_mock.assert_called_once_with(1.0)

    def test_unload_checkpoint_serialization_lock(self, forge_client, monkeypatch):
        import time
        from concurrent.futures import ThreadPoolExecutor

        forge_client._checkpoint_unloaded = False
        execution_order = []

        def slow_post(url, *args, **kwargs):
            if "unload-checkpoint" in url:
                execution_order.append("start")
                time.sleep(0.05)
                execution_order.append("end")
            return MagicMock(status_code=200)

        monkeypatch.setattr("requests.post", slow_post)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(forge_client.unload_checkpoint)
            f2 = executor.submit(forge_client.unload_checkpoint)
            f1.result()
            f2.result()

        # One thread runs, sets _checkpoint_unloaded = True, second thread sees it's already unloaded
        assert execution_order == ["start", "end"]

    def test_finalize_forge_generation_skips_duplicate_unload_when_already_unloaded(self, monkeypatch):
        from services.vram_arbiter import VRAMArbiter
        from ui.controllers.hardware_engine import HardwareEngineMixin

        class DummyEngine(HardwareEngineMixin):
            def __init__(self):
                self.forge_client = MagicMock()
                self.forge_client._checkpoint_unloaded = True
                self._reload_active_lms_model_async = MagicMock()

        engine = DummyEngine()
        arbiter = VRAMArbiter.get_instance()
        release_mock = MagicMock()
        monkeypatch.setattr(arbiter, "release_diffusion_for_llm", release_mock)

        engine._finalize_forge_generation()
        release_mock.assert_called_once_with(force=False)

    def test_finalize_forge_generation_forces_unload_when_not_unloaded(self, monkeypatch):
        from services.vram_arbiter import VRAMArbiter
        from ui.controllers.hardware_engine import HardwareEngineMixin

        class DummyEngine(HardwareEngineMixin):
            def __init__(self):
                self.forge_client = MagicMock()
                self.forge_client._checkpoint_unloaded = False
                self._reload_active_lms_model_async = MagicMock()

        engine = DummyEngine()
        arbiter = VRAMArbiter.get_instance()
        release_mock = MagicMock()
        monkeypatch.setattr(arbiter, "release_diffusion_for_llm", release_mock)

        engine._finalize_forge_generation()
        release_mock.assert_called_once_with(force=True)

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

    def test_free_vram_404_fallback(self, forge_client, monkeypatch):
        mock_resp = MagicMock(status_code=404)
        post_mock = MagicMock(return_value=mock_resp)
        monkeypatch.setattr("requests.post", post_mock)
        assert forge_client._free_vram_supported is True

        # First call: receives 404, should flag unsupported and return False
        res1 = forge_client.free_vram(always_offload=True)
        assert res1 is False
        assert forge_client._free_vram_supported is False
        assert post_mock.call_count == 1

        # Subsequent call: short-circuits without network call
        res2 = forge_client.free_vram(always_offload=True)
        assert res2 is False
        assert post_mock.call_count == 1

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
        monkeypatch.setattr("services.art_director.vram_policy.resolve_hardware_vram_budget", lambda *a, **kw: (8.0, 700))
        monkeypatch.setattr("services.art_director.vram_policy.should_enable_pin_shared_memory", lambda **kw: (True, "ok"))
        monkeypatch.setattr("config.FORGE_EXTRA_ARGS", ["--opt-sdp-attention", "--xformers"])
        monkeypatch.setattr("services.forge_client.get_clean_forge_env", lambda: {"TEST": "1"})

        mock_popen = MagicMock()
        monkeypatch.setattr("subprocess.Popen", mock_popen)
        res = ForgeClient.launch_forge_process_silently(launcher_path=launcher)
        assert res is not None
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "cmd.exe" in cmd[0]
        assert "--pin-shared-memory" in cmd
        assert "--always-offload-from-vram" not in cmd
        assert "--xformers" in cmd

    def test_txt2img_updates_free_vram_on_inf_mem_change_and_stage_downgrade(self, forge_client, monkeypatch):
        img_b64 = _create_valid_png_b64(64, 64)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"images": [img_b64], "info": "{}"}

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        monkeypatch.setattr(forge_client, "set_model", lambda ckpt: True)
        monkeypatch.setattr(forge_client, "unload_checkpoint", lambda: True)
        monkeypatch.setattr("services.art_director.vram_policy.resolve_hardware_vram_budget", lambda *a, **kw: (8.0, 700))
        monkeypatch.setattr("services.art_director.vram_policy.resolve_checkpoint_file_size_mb", lambda *a, **kw: None)

        free_calls = []
        monkeypatch.setattr(
            forge_client,
            "free_vram",
            lambda **kw: free_calls.append(kw) or True,
        )

        # Патчим requests.Session.post напрямую, т.к. у TestVRAMManagement нет _patch_session_post
        mock_session_cls = MagicMock()
        mock_session_cls.return_value.post.return_value = mock_resp
        monkeypatch.setattr("requests.Session", mock_session_cls)

        # 1. SD 1.5 базовый (FAST_KEEP_VRAM, inf_mem=640, always_offload=False)
        forge_client.text_to_image(prompt="1girl", width=512, height=768, enable_hr=False)
        assert free_calls[-1] == {"always_offload": False, "inference_memory_mb": 640, "unload_models": False}

        # 2. SD 1.5 с Hires (always_offload остался False, но inf_mem вырос до 768 -> обязан отправить free_vram!)
        forge_client.text_to_image(prompt="1girl", width=512, height=768, enable_hr=True, hr_scale=1.5)
        assert free_calls[-1] == {"always_offload": False, "inference_memory_mb": 768, "unload_models": False}

        # 3. Понижение в STAGE_OFFLOAD (SDXL FP16 + ADetailer) -> unload_models=True
        monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: {"forge_sdxl_fp8_enabled": False})
        forge_client.text_to_image(
            prompt="1girl",
            checkpoint="sdxl_base.safetensors",
            width=896,
            height=1152,
            enable_hr=False,
            alwayson_scripts={"ADetailer": {"args": [True, False, {"ad_model": "face_yolov8m.pt", "ad_inpaint_width": 768}]}},
        )
        assert free_calls[-1]["always_offload"] is True
        assert free_calls[-1]["unload_models"] is True

    def test_txt2img_sdxl_fp8_1024_injects_never_oom_vae_tiling_and_skips_duplicate_override(self, forge_client, monkeypatch):
        img_b64 = _create_valid_png_b64(64, 64)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"images": [img_b64], "info": "{}"}

        monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: {"forge_sdxl_fp8_enabled": True})
        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        monkeypatch.setattr(forge_client, "get_models", lambda: [{"title": "dreamshaperXL.safetensors", "model_name": "dreamshaperXL", "filename": "dreamshaperXL.safetensors"}])
        monkeypatch.setattr(forge_client, "get_current_model", lambda: "sd15.safetensors")
        monkeypatch.setattr(forge_client, "unload_checkpoint", lambda: True)
        monkeypatch.setattr("services.art_director.vram_policy.resolve_hardware_vram_budget", lambda *a, **kw: (8.0, 150))
        monkeypatch.setattr("services.art_director.vram_policy.resolve_checkpoint_file_size_mb", lambda *a, **kw: 6500.0)

        def mock_get(url, *a, **kw):
            r = MagicMock(status_code=200)
            if "scripts" in url:
                r.json.return_value = {"txt2img": ["adetailer", "never oom integrated"], "img2img": []}
            else:
                r.json.return_value = {}
            return r

        options_posts = []
        free_calls = []

        def mock_post(url, *a, **kw):
            if "options" in url:
                options_posts.append(kw.get("json", {}))
            elif "free-vram" in url:
                free_calls.append(kw.get("json", {}))
            return MagicMock(status_code=200)

        monkeypatch.setattr("requests.get", mock_get)
        monkeypatch.setattr("requests.post", mock_post)

        txt2img_payloads = []
        mock_session_cls = MagicMock()

        def mock_session_post(url, *a, **kw):
            txt2img_payloads.append(kw.get("json", {}))
            return mock_resp

        mock_session_cls.return_value.post.side_effect = mock_session_post
        monkeypatch.setattr("requests.Session", mock_session_cls)

        forge_client.text_to_image(
            prompt="1girl",
            checkpoint="dreamshaperXL.safetensors",
            width=1024,
            height=1024,
            enable_hr=False,
            alwayson_scripts={"ADetailer": {"args": [True, False, {"ad_model": "face_yolov8m.pt", "ad_inpaint_width": 768}]}},
        )

        # 1. set_model 1 раз отправил /options; override_settings в txt2img не дублирует чекпоинт/dtype
        assert len(options_posts) == 1
        assert options_posts[0]["forge_unet_storage_dtype"] == "fp8e4"
        override = txt2img_payloads[0].get("override_settings", {})
        assert "sd_model_checkpoint" not in override
        assert "forge_unet_storage_dtype" not in override

        # 2. Never OOM Integrated (VAE Always Tiled) и inf_mem=512MB для сохранения UNet/CLIP под ADetailer
        assert txt2img_payloads[0]["alwayson_scripts"]["Never OOM Integrated"] == {"args": [False, True]}
        assert free_calls[-1]["always_offload"] is False
        assert free_calls[-1]["inference_memory_mb"] == 512


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

    def test_txt2img_logs_warning_and_retries_on_black_frame_success(self, forge_client, tmp_path, monkeypatch, caplog):
        """Если получен чёрный кадр < 10000 байт с разрешением >= 512x512, пишется warning и выполняется успешный ретрай."""
        buf = io.BytesIO()
        Image.new("RGB", (512, 512), color="black").save(buf, format="PNG")
        black_bytes = buf.getvalue()
        assert len(black_bytes) < 10_000

        black_b64 = base64.b64encode(black_bytes).decode("utf-8")
        mock_resp_black = MagicMock(status_code=200)
        mock_resp_black.json.return_value = {"images": [black_b64], "info": "{}"}

        # Генерируем валидный PNG > 10000 байт (с шумом)
        import os
        valid_buf = io.BytesIO()
        noise_bytes = os.urandom(512 * 512 * 3)
        Image.frombytes("RGB", (512, 512), noise_bytes).save(valid_buf, format="PNG")
        valid_bytes = valid_buf.getvalue()
        assert len(valid_bytes) >= 10_000
        valid_b64 = base64.b64encode(valid_bytes).decode("utf-8")
        mock_resp_valid = MagicMock(status_code=200)
        mock_resp_valid.json.return_value = {"images": [valid_b64], "info": "{}"}

        resp_cycle = [mock_resp_black, mock_resp_valid]
        def _mock_post(url, *a, **k):
            if "/sdapi/v1/txt2img" in url:
                return resp_cycle.pop(0) if resp_cycle else mock_resp_valid
            return MagicMock(status_code=200, json=lambda: {})

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        monkeypatch.setattr(forge_client, "set_model", lambda ckpt: True)
        self._patch_session_post(monkeypatch, _mock_post)

        import logging
        with caplog.at_level(logging.WARNING):
            out_file = forge_client.text_to_image(
                prompt="test black frame with retry",
                width=512,
                height=512,
                output_dir=tmp_path / "black_out"
            )

        assert out_file.exists()
        assert any("Обнаружен пустой/чёрный кадр (возможное NaN-переполнение в VAE)" in record.message for record in caplog.records)

    def test_txt2img_black_frame_retry_fails_raises_runtime_error(self, forge_client, tmp_path, monkeypatch, caplog):
        """Если и после восстановительного ретрая вернулся чёрный кадр — выбрасывается RuntimeError и файл не сохраняется."""
        buf = io.BytesIO()
        Image.new("RGB", (512, 512), color="black").save(buf, format="PNG")
        black_bytes = buf.getvalue()
        assert len(black_bytes) < 10_000

        black_b64 = base64.b64encode(black_bytes).decode("utf-8")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"images": [black_b64], "info": "{}"}

        monkeypatch.setattr(forge_client, "is_server_ready", lambda timeout=1.0: True)
        monkeypatch.setattr(forge_client, "set_model", lambda ckpt: True)
        self._patch_session_post(monkeypatch, lambda url, *a, **k: mock_resp)

        out_dir = tmp_path / "black_fail_out"
        with pytest.raises(RuntimeError) as exc_info:
            forge_client.text_to_image(
                prompt="test persistent black frame",
                width=512,
                height=512,
                output_dir=out_dir
            )

        assert "Forge вернул пустой/чёрный кадр (NaN в VAE) даже после восстановительного ретрая" in str(exc_info.value)
        if out_dir.exists():
            assert len(list(out_dir.glob("*.png"))) == 0

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
        assert forge_client.unload_checkpoint() is False

        forge_client._checkpoint_unloaded = False
        monkeypatch.setattr("requests.post", MagicMock(side_effect=Exception("unload fail")))
        assert forge_client.unload_checkpoint() is False

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

