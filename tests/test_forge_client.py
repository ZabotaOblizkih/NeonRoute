"""
Tests for ForgeClient:
- ADetailer Guard (hardware protection for 8GB VRAM on SDXL/MoP)
- UNet storage dtype injection (FP8 e4m3fn as 'fp8e4' for SDXL, 'Auto' for SD 1.5)
- Model architecture detection helper (is_sdxl_checkpoint)
"""

import base64
import pytest
import requests
from unittest.mock import patch, MagicMock

from services.forge_client import ForgeClient, is_sdxl_checkpoint

DUMMY_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200).decode("ascii")


def _mock_progress_get(*args, **kwargs):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"progress": 1.0, "state": {}}
    return resp


class TestIsSdxlCheckpointHelper:
    """Проверка универсального хелпера детекции SDXL архитектуры."""

    def test_sdxl_markers_detection(self):
        # Маркеры: "xl", "sdxl", "pony", "dmd", "insta", "mop" (регистронезависимо)
        assert is_sdxl_checkpoint("copaxTimelessxlSDXL1_v8.safetensors") is True
        assert is_sdxl_checkpoint("mopMixtureOfPerverts_instaV10DMD.safetensors") is True
        assert is_sdxl_checkpoint("PonyDiffusionV6XL.safetensors") is True
        assert is_sdxl_checkpoint("sd_xl_base_1.0.safetensors") is True
        assert is_sdxl_checkpoint("dmd2_sdxl_4step.safetensors") is True
        assert is_sdxl_checkpoint("insta_camera_v1.safetensors") is True
        assert is_sdxl_checkpoint("mop_special_v2.safetensors") is True

    def test_sd15_checkpoints_not_sdxl(self):
        assert is_sdxl_checkpoint("realisticVisionV60B1_v51HyperVAE_pruned_fp16.safetensors") is False
        assert is_sdxl_checkpoint("cyberrealistic_classic.safetensors") is False
        assert is_sdxl_checkpoint("dreamshaper_8.safetensors") is False
        assert is_sdxl_checkpoint("meinahentai_v5Final.safetensors") is False
        assert is_sdxl_checkpoint("uberRealisticPornMerge_v23Final.safetensors") is False

    def test_empty_or_none(self):
        assert is_sdxl_checkpoint("") is False
        assert is_sdxl_checkpoint(None) is False


class TestADetailerGuard:
    """Проверка аппаратного ADetailer Guard на 8GB VRAM."""

    def test_detect_adetailer_needed_returns_false_for_sdxl_and_mop(self):
        client = ForgeClient()

        # SDXL модели должны аппаратно блокировать ADetailer независимо от промпта
        assert client._detect_adetailer_needed("photo of 1girl portrait", "copaxTimelessxlSDXL1_v8.safetensors") is False
        assert client._detect_adetailer_needed("1girl smiling face", "mopMixtureOfPerverts_instaV10DMD.safetensors") is False
        assert client._detect_adetailer_needed("1girl, portrait", "sdxl_base.safetensors") is False
        assert client._detect_adetailer_needed("1girl, portrait", "ponyDiffusionV6.safetensors") is False

    def test_detect_adetailer_needed_returns_true_for_sd15_human(self):
        client = ForgeClient()
        # Для SD 1.5 с человеком в кадре ADetailer должен оставаться включенным
        assert client._detect_adetailer_needed("photo of 1girl portrait", "realisticVisionV60B1.safetensors") is True

    def test_text_to_image_preserves_adetailer_for_sdxl(self, tmp_path):
        client = ForgeClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"images": [DUMMY_PNG_B64], "info": "{}"}

        sent_payloads = []

        def fake_session_post(session_self, url, *args, **kwargs):
            if "txt2img" in url:
                sent_payloads.append(kwargs.get("json", {}))
                return mock_resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {}
            return resp

        with patch("requests.get", side_effect=_mock_progress_get), \
             patch.object(client, "is_server_ready", return_value=True), \
             patch.object(client, "_wait_for_vram_clear"), \
             patch.object(requests.Session, "post", side_effect=fake_session_post, autospec=True), \
             patch.object(client, "get_models", return_value=[{"title": "sdxl_model.safetensors", "model_name": "sdxl"}]), \
             patch.object(client, "get_current_model", return_value="sdxl_model.safetensors"):

            client.text_to_image(
                prompt="portrait of 1girl",
                checkpoint="sdxl_model.safetensors",
                output_dir=tmp_path,
                alwayson_scripts={"ADetailer": {"args": [True]}}
            )

        assert len(sent_payloads) == 1
        payload = sent_payloads[0]
        # ADetailer должен быть вычищен из alwayson_scripts
        assert "ADetailer" in payload.get("alwayson_scripts", {})


class TestUnetStorageDtypeInjection:
    """Проверка инжекции forge_unet_storage_dtype ('fp8e4' для SDXL, 'Auto' для SD 1.5)."""

    def test_set_model_sdxl_sends_fp8e4(self):
        client = ForgeClient()
        options_payloads = []

        def fake_post(url, *args, **kwargs):
            if "options" in url:
                options_payloads.append(kwargs.get("json", {}))
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {}
            return resp

        with patch("requests.post", side_effect=fake_post), \
             patch.object(client, "_wait_for_vram_clear"), \
             patch.object(client, "get_models", return_value=[{"title": "copaxTimelessxlSDXL1_v8.safetensors", "model_name": "copax"}]), \
             patch.object(client, "get_current_model", return_value=None):

            res = client.set_model("copaxTimelessxlSDXL1_v8.safetensors")
            assert res is True

        assert len(options_payloads) == 1
        opts = options_payloads[0]
        assert opts["sd_model_checkpoint"] == "copaxTimelessxlSDXL1_v8.safetensors"
        assert opts["forge_unet_storage_dtype"] == "fp8e4"

    def test_set_active_checkpoint_alias(self):
        client = ForgeClient()
        options_payloads = []

        def fake_post(url, *args, **kwargs):
            if "options" in url:
                options_payloads.append(kwargs.get("json", {}))
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {}
            return resp

        with patch("requests.post", side_effect=fake_post), \
             patch.object(client, "_wait_for_vram_clear"), \
             patch.object(client, "get_models", return_value=[{"title": "copaxTimelessxlSDXL1_v8.safetensors", "model_name": "copax"}]), \
             patch.object(client, "get_current_model", return_value=None):

            res = client.set_active_checkpoint("copaxTimelessxlSDXL1_v8.safetensors")
            assert res is True

        assert len(options_payloads) == 1
        opts = options_payloads[0]
        assert opts["forge_unet_storage_dtype"] == "fp8e4"

    def test_set_model_sd15_sends_auto(self):
        client = ForgeClient()
        options_payloads = []

        def fake_post(url, *args, **kwargs):
            if "options" in url:
                options_payloads.append(kwargs.get("json", {}))
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {}
            return resp

        with patch("requests.post", side_effect=fake_post), \
             patch.object(client, "_wait_for_vram_clear"), \
             patch.object(client, "get_models", return_value=[{"title": "realisticVisionV60B1.safetensors", "model_name": "rv"}]), \
             patch.object(client, "get_current_model", return_value=None):

            res = client.set_model("realisticVisionV60B1.safetensors")
            assert res is True

        assert len(options_payloads) == 1
        opts = options_payloads[0]
        assert opts["sd_model_checkpoint"] == "realisticVisionV60B1.safetensors"
        assert opts["forge_unet_storage_dtype"] == "Auto"

    def test_txt2img_sdxl_override_settings_sends_fp8e4(self, tmp_path):
        client = ForgeClient()
        sent_payloads = []

        def fake_session_post(session_self, url, *args, **kwargs):
            if "txt2img" in url:
                sent_payloads.append(kwargs.get("json", {}))
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"images": [DUMMY_PNG_B64], "info": "{}"}
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {}
            return resp

        with patch("requests.get", side_effect=_mock_progress_get), \
             patch.object(client, "is_server_ready", return_value=True), \
             patch.object(client, "_wait_for_vram_clear"), \
             patch.object(requests.Session, "post", side_effect=fake_session_post, autospec=True), \
             patch.object(client, "get_models", return_value=[{"title": "mopMixtureOfPerverts_instaV10DMD.safetensors", "model_name": "mop"}]), \
             patch.object(client, "get_current_model", return_value=None):

            client.text_to_image(
                prompt="a beautiful landscape",
                checkpoint="mopMixtureOfPerverts_instaV10DMD.safetensors",
                output_dir=tmp_path
            )

        assert len(sent_payloads) == 1
        payload = sent_payloads[0]
        override_settings = payload.get("override_settings", {})
        assert override_settings.get("forge_unet_storage_dtype") == "fp8e4"

    def test_txt2img_sd15_override_settings_sends_auto(self, tmp_path):
        client = ForgeClient()
        sent_payloads = []

        def fake_session_post(session_self, url, *args, **kwargs):
            if "txt2img" in url:
                sent_payloads.append(kwargs.get("json", {}))
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"images": [DUMMY_PNG_B64], "info": "{}"}
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {}
            return resp

        with patch("requests.get", side_effect=_mock_progress_get), \
             patch.object(client, "is_server_ready", return_value=True), \
             patch.object(client, "_wait_for_vram_clear"), \
             patch.object(requests.Session, "post", side_effect=fake_session_post, autospec=True), \
             patch.object(client, "get_models", return_value=[{"title": "cyberrealistic_classic.safetensors", "model_name": "cyber"}]), \
             patch.object(client, "get_current_model", return_value=None):

            client.text_to_image(
                prompt="a beautiful landscape",
                checkpoint="cyberrealistic_classic.safetensors",
                output_dir=tmp_path
            )

        assert len(sent_payloads) == 1
        payload = sent_payloads[0]
        override_settings = payload.get("override_settings", {})
        assert override_settings.get("forge_unet_storage_dtype") == "Auto"

    def test_sdxl_fp8_disabled_config_sends_auto(self):
        client = ForgeClient()
        options_payloads = []

        def fake_post(url, *args, **kwargs):
            if "options" in url:
                options_payloads.append(kwargs.get("json", {}))
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {}
            return resp

        with patch("requests.post", side_effect=fake_post), \
             patch.object(client, "_wait_for_vram_clear"), \
             patch("utils.config_manager.ConfigManager.load_config", return_value={"forge_sdxl_fp8_enabled": False}), \
             patch.object(client, "get_models", return_value=[{"title": "sdxl_model.safetensors", "model_name": "sdxl"}]), \
             patch.object(client, "get_current_model", return_value=None):

            client.set_model("sdxl_model.safetensors")

        assert len(options_payloads) == 1
        opts = options_payloads[0]
        assert opts["forge_unet_storage_dtype"] == "Auto"
