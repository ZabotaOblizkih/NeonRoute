"""tests/smoke/test_forge_vram_profiles_smoke.py

Сквозной параметризованный смоук-сьют краевых аппаратных профилей памяти
(ProcessManager -> ArtDirector -> vram_policy -> VRAMArbiter -> ForgeClient.text_to_image)
для классов моделей SD 1.5, SDXL FP8 и SDXL FP16 Heavy.
"""

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

import services.art_director.vram_policy as vram_policy
from services.art_director.contracts import VisualIntent
from services.art_director.director import ArtDirector
from services.art_director_config import save_art_director_models
from services.forge_client import ForgeClient
from services.model_router import ModelRouter
from services.process_manager import ProcessManager
from services.vram_arbiter import GPUOwner, VRAMArbiter


def _make_valid_png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(24, 32, 48)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


VALID_PNG_B64 = _make_valid_png_b64()

MODEL_SPECS = {
    "sd15": {
        "id": "smoke_sd15",
        "name": "DreamShaper 8",
        "architecture": "sd15",
        "checkpoint": "dreamshaper_8.safetensors",
        "fp8": False,
        "routing_description": "SD 1.5 versatile digital illustration",
        "active": True,
        "pipeline": {
            "architecture": "sd15",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": 25,
            "cfg_scale": 7.0,
            "clip_skip": 2,
            "enable_hr": True,
            "hr_scale": 1.5,
            "hires_upscaler": "R-ESRGAN 4x+",
            "hires_denoise": 0.35,
            "enable_adetailer": True,
            "adetailer_face_model": "face_yolov8m.pt",
            "adetailer_face_inpaint_size": 512,
            "adetailer_hands_enabled": True,
            "adetailer_hands_model": "hand_yolov9c.pt",
            "adetailer_hands_inpaint_size": 512,
        },
    },
    "sdxl_fp8": {
        "id": "smoke_sdxl_fp8",
        "name": "DreamShaper XL Lightning FP8",
        "architecture": "sdxl",
        "checkpoint": "dreamshaperXL_lightningDPMSDE.safetensors",
        "fp8": True,
        "routing_description": "SDXL Lightning FP8 photorealistic portrait",
        "active": True,
        "pipeline": {
            "architecture": "sdxl",
            "sampler": "DPM++ SDE",
            "scheduler": "Karras",
            "steps": 8,
            "cfg_scale": 2.5,
            "clip_skip": 1,
            "enable_hr": False,
            "hr_scale": 1.0,
            "enable_adetailer": True,
            "adetailer_face_model": "face_yolov8m.pt",
            "adetailer_face_inpaint_size": 768,
            "adetailer_hands_enabled": True,
            "adetailer_hands_model": "hand_yolov9c.pt",
            "adetailer_hands_inpaint_size": 512,
        },
    },
    "sdxl_fp16": {
        "id": "smoke_sdxl_fp16",
        "name": "Juggernaut XL v9 FP16",
        "architecture": "sdxl",
        "checkpoint": "juggernautXL_v9.safetensors",
        "fp8": False,
        "routing_description": "SDXL FP16 heavy cinematic realism",
        "active": True,
        "pipeline": {
            "architecture": "sdxl",
            "sampler": "DPM++ 2M",
            "scheduler": "Karras",
            "steps": 28,
            "cfg_scale": 6.0,
            "clip_skip": 1,
            "enable_hr": True,
            "hr_scale": 1.25,
            "hires_upscaler": "R-ESRGAN 4x+",
            "hires_denoise": 0.32,
            "enable_adetailer": True,
            "adetailer_face_model": "face_yolov8m.pt",
            "adetailer_face_inpaint_size": 768,
            "adetailer_hands_enabled": True,
            "adetailer_hands_model": "hand_yolov9c.pt",
            "adetailer_hands_inpaint_size": 512,
        },
    },
}


class ForgeApiSpy:
    """Перехватчик сетевых вызовов Forge API и ProcessManager.Popen без реальной сети/процессов."""

    def __init__(self, models_list):
        self.models_list = models_list
        self.current_model = models_list[0]["title"]
        self.free_vram_calls = []
        self.options_calls = []
        self.txt2img_calls = []
        self.unload_calls = []
        self.popen_cmds = []


@pytest.fixture
def smoke_harness(tmp_path, monkeypatch):
    """Полностью изолированный стенд ProcessManager -> ArtDirector -> vram_policy -> ForgeClient."""
    QApplication.instance() or QApplication([])

    models_json_path = tmp_path / "data" / "art_director_models.json"
    models_json_path.parent.mkdir(parents=True, exist_ok=True)
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    fake_forge_dir = tmp_path / "fake_forge"
    fake_models_dir = fake_forge_dir / "models" / "Stable-diffusion"
    fake_models_dir.mkdir(parents=True, exist_ok=True)
    fake_launcher = fake_forge_dir / "forge_launcher.bat"
    fake_launcher.write_text("@echo off\nexit /b 0\n", encoding="utf-8")

    def _reset_router_singleton():
        inst = getattr(ModelRouter, "_instance", None)
        if inst is not None and hasattr(inst, "cancel_deferred_warmup"):
            inst.cancel_deferred_warmup()
        ModelRouter._instance = None

    # Пустые маркер-файлы чекпоинтов не требуются, но пусть пути разрешаются чисто в tmp_path
    _reset_router_singleton()
    ForgeClient._instance = None
    VRAMArbiter._instance = None
    vram_policy._hardware_budget_cache = None
    # Подавляем фоновый 5s прогрев ONNX-эмбеддингов в смоук-гарнесе
    monkeypatch.setattr(ModelRouter, "update_embeddings_async", lambda self, delay=5.0: None)

    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", models_json_path)
    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", models_json_path)
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", models_json_path)
    monkeypatch.setattr("config.OUTPUTS_DIR", outputs_dir)
    monkeypatch.setattr("services.forge_client.OUTPUTS_DIR", outputs_dir)
    monkeypatch.setattr("config.LOGS_DIR", logs_dir)
    monkeypatch.setattr("services.process_manager.LOGS_DIR", logs_dir)
    monkeypatch.setattr("config.FORGE_ROOT_DIR", fake_forge_dir)
    monkeypatch.setattr("services.forge_client.FORGE_ROOT_DIR", fake_forge_dir)
    monkeypatch.setattr("config.FORGE_LAUNCHER_PATH", fake_launcher)
    monkeypatch.setattr("services.process_manager.FORGE_LAUNCHER_PATH", fake_launcher)
    monkeypatch.setattr("config.FORGE_EXTRA_ARGS", [])
    monkeypatch.setattr("services.forge_client.get_clean_forge_env", lambda: {"TEST": "1"})
    monkeypatch.setattr("services.process_manager.get_clean_forge_env", lambda: {"TEST": "1"})
    monkeypatch.setattr("services.process_manager.verify_forge_http_handshake", lambda *a, **kw: True)
    monkeypatch.setattr("services.process_manager.time.sleep", lambda *a, **kw: None)

    save_art_director_models(list(MODEL_SPECS.values()))

    forge_models_api = [
        {
            "title": spec["checkpoint"],
            "model_name": Path(spec["checkpoint"]).stem,
            "filename": str(fake_models_dir / spec["checkpoint"]),
        }
        for spec in MODEL_SPECS.values()
    ]
    spy = ForgeApiSpy(forge_models_api)

    # Полная изоляция сети: socket.create_connection, requests.get/post, requests.Session
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: MagicMock())

    def mock_get(url, *a, **kw):
        resp = MagicMock(status_code=200)
        if "/sdapi/v1/sd-models" in url:
            resp.json.return_value = list(spy.models_list)
        elif "/sdapi/v1/options" in url:
            resp.json.return_value = {"sd_model_checkpoint": spy.current_model}
        elif "/sdapi/v1/upscalers" in url:
            resp.json.return_value = [{"name": "R-ESRGAN 4x+"}, {"name": "Latent"}]
        elif "/sdapi/v1/scripts" in url:
            resp.json.return_value = {
                "txt2img": ["adetailer", "never oom integrated"],
                "img2img": ["adetailer", "never oom integrated"],
            }
        elif "/sdapi/v1/embeddings" in url:
            resp.json.return_value = {"loaded": {}}
        elif "/sdapi/v1/progress" in url:
            resp.json.return_value = {"progress": 1.0, "state": {"sampling_step": 1, "sampling_steps": 1, "textinfo": ""}}
        else:
            resp.json.return_value = {}
        return resp

    def mock_post(url, *a, **kw):
        payload = kw.get("json") or {}
        if "/sdapi/v1/free-vram" in url:
            spy.free_vram_calls.append(dict(payload))
        elif "/sdapi/v1/options" in url:
            spy.options_calls.append(dict(payload))
            if "sd_model_checkpoint" in payload:
                spy.current_model = payload["sd_model_checkpoint"]
        elif "/sdapi/v1/unload-checkpoint" in url:
            spy.unload_calls.append(True)
        resp = MagicMock(status_code=200)
        resp.json.return_value = {}
        return resp

    class _MockSession:
        def post(self, url, *a, **kw):
            payload = kw.get("json") or {}
            if "/sdapi/v1/txt2img" in url:
                spy.txt2img_calls.append(payload)
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"images": [VALID_PNG_B64], "info": json.dumps({"seed": 42})}
            return resp

        def close(self):
            pass

    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("requests.post", mock_post)
    monkeypatch.setattr("requests.Session", lambda: _MockSession())

    virtual_clock = [10000.0]

    def _fast_sleep(sec):
        virtual_clock[0] += float(sec or 0.0)

    def _fast_time():
        virtual_clock[0] += 0.001
        return virtual_clock[0]

    monkeypatch.setattr("services.forge_client.time.sleep", _fast_sleep)
    monkeypatch.setattr("services.forge_client.time.time", _fast_time)

    mock_proc = MagicMock()
    mock_proc.pid = 4242
    mock_proc.poll.return_value = None
    mock_proc.stdout.readline.return_value = ""

    def mock_popen(cmd, *a, **kw):
        spy.popen_cmds.append(list(cmd))
        return mock_proc

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    monkeypatch.setattr("services.art_director.vram_policy.resolve_checkpoint_file_size_mb", lambda *a, **kw: None)

    def configure_hardware(total_vram_mb: int, total_ram_mb: int, avail_ram_mb: int, os_reserve_mb: int = 700, sdxl_fp8: bool = True):
        vram_policy._hardware_budget_cache = None
        free_vram_mb = max(512, total_vram_mb - os_reserve_mb)
        total_vram_gb = round(total_vram_mb / 1024.0, 2)

        fake_monitor = SimpleNamespace(
            nvml_initialized=True,
            _last_emitted_stats={
                "vram_total_mb": total_vram_mb,
                "vram_used_mb": os_reserve_mb,
                "ram_total_mb": total_ram_mb,
            },
            get_total_vram_mb=lambda: total_vram_mb,
            get_free_vram_mb=lambda: free_vram_mb,
        )
        fake_vm = SimpleNamespace(
            total=total_ram_mb * 1024 * 1024,
            available=avail_ram_mb * 1024 * 1024,
        )

        monkeypatch.setattr("psutil.virtual_memory", lambda: fake_vm)
        monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: (False, os_reserve_mb))
        monkeypatch.setattr(
            "services.art_director.vram_policy.resolve_hardware_vram_budget",
            lambda *a, **kw: (total_vram_gb, os_reserve_mb),
        )
        monkeypatch.setattr(
            "utils.config_manager.ConfigManager.load_config",
            lambda: {"forge_sdxl_fp8_enabled": sdxl_fp8},
        )
        return fake_monitor

    yield SimpleNamespace(
        tmp_path=tmp_path,
        outputs_dir=outputs_dir,
        fake_launcher=fake_launcher,
        spy=spy,
        configure_hardware=configure_hardware,
    )

    _reset_router_singleton()
    ForgeClient._instance = None
    VRAMArbiter._instance = None
    vram_policy._hardware_budget_cache = None


def _run_full_chain(harness, model_key: str, total_vram_mb: int, total_ram_mb: int, avail_ram_mb: int):
    spec = MODEL_SPECS[model_key]
    monitor = harness.configure_hardware(
        total_vram_mb=total_vram_mb,
        total_ram_mb=total_ram_mb,
        avail_ram_mb=avail_ram_mb,
        sdxl_fp8=spec["fp8"],
    )

    # 1. ProcessManager: сборка стартовой команды Forge
    pm = ProcessManager()
    pm.is_port_open = MagicMock(side_effect=[False, True])
    with patch("services.asset_validator.AssetValidator.log_validation_summary"):
        pm._run_forge_start()
    launch_cmd = harness.spy.popen_cmds[-1]

    # 2. ArtDirector: планирование с учётом VRAM-политики и Hires.fix gate
    ad = ArtDirector()
    ad.system_monitor = monitor
    intent = VisualIntent(
        prompt="1girl, portrait in neon rain, detailed face, hands",
        suggested_model=spec["id"],
        seed=12345,
        mode="assistant",
    )
    plan = ad.plan_from_visual_intent(
        visual_intent=intent,
        preferred_style=spec["id"],
        available_models=harness.spy.models_list,
    )

    # 3. ForgeClient.text_to_image: расчёт политики, вызов /free-vram и /txt2img
    fc = ForgeClient(system_monitor=monitor)
    kwargs = plan.to_forge_kwargs(output_dir=harness.outputs_dir)
    out_file = fc.text_to_image(**kwargs)

    # Забираем последний вызов политики и txt2img
    free_call = harness.spy.free_vram_calls[-2] if len(harness.spy.free_vram_calls) >= 2 else harness.spy.free_vram_calls[-1]
    # Точный вызов перед txt2img — последний с unload_models=False (либо предпоследний до финального unload_checkpoint)
    pre_gen_free_calls = [c for c in harness.spy.free_vram_calls if "always_offload" in c]
    assert pre_gen_free_calls, "ForgeClient не отправил политику памяти в /sdapi/v1/free-vram"

    return {
        "launch_cmd": launch_cmd,
        "plan": plan,
        "out_file": out_file,
        "pre_free": pre_gen_free_calls[-1],
        "txt2img": harness.spy.txt2img_calls[-1],
        "options": list(harness.spy.options_calls),
    }


# =============================================================================
# 1. Матрица 5 уровней VRAM x 3 класса моделей
# =============================================================================

@pytest.mark.parametrize("model_key", ["sd15", "sdxl_fp8", "sdxl_fp16"])
def test_vram_profile_2gb_extreme_low_end(smoke_harness, model_key):
    """2 ГБ VRAM (2048 МБ): строго STAGE_OFFLOAD, Hires.fix заблокирован, VAE tiling активен."""
    res = _run_full_chain(smoke_harness, model_key, total_vram_mb=2048, total_ram_mb=16384, avail_ram_mb=10000)

    assert res["out_file"].exists()
    assert res["plan"].enable_hr is False
    assert res["txt2img"]["enable_hr"] is False
    assert res["pre_free"]["always_offload"] is True

    # Контракт ТЗ 2.1 / архитектурный контракт: для 2 ГБ VRAM должен быть включён VAE tiling
    # исключительно через alwayson_scripts["Never OOM Integrated"] (args=[False, True]),
    # без недопустимых ключей forge_vae_tiling / sd_vae_tile в payload или override_settings.
    never_oom_args = (res["txt2img"].get("alwayson_scripts") or {}).get("Never OOM Integrated", {}).get("args")
    assert never_oom_args == [False, True], "На экстремальных 2 ГБ VRAM должен включаться VAE tiling через Never OOM Integrated"
    assert "forge_vae_tiling" not in res["txt2img"]
    assert "sd_vae_tile" not in res["txt2img"]
    override = res["txt2img"].get("override_settings") or {}
    assert "forge_vae_tiling" not in override
    assert "sd_vae_tile" not in override


@pytest.mark.parametrize("model_key", ["sd15", "sdxl_fp8", "sdxl_fp16"])
def test_vram_profile_4gb_mobile_entry(smoke_harness, model_key):
    """4 ГБ VRAM (4096 МБ): Hires.fix запрещен; SDXL строго STAGE_OFFLOAD, SD 1.5 либо STAGE_OFFLOAD, либо без HR."""
    res = _run_full_chain(smoke_harness, model_key, total_vram_mb=4096, total_ram_mb=16384, avail_ram_mb=10000)

    assert res["out_file"].exists()
    assert res["plan"].enable_hr is False
    assert res["txt2img"]["enable_hr"] is False
    if model_key in ("sdxl_fp8", "sdxl_fp16"):
        assert res["pre_free"]["always_offload"] is True
    else:
        assert res["pre_free"]["always_offload"] is True or res["plan"].enable_hr is False


def test_vram_profile_6gb_mid_tier(smoke_harness):
    """6 ГБ VRAM (6144 МБ): SD 1.5 в FAST_KEEP_VRAM, SDXL FP8 в SMART_SWAP/STAGE_OFFLOAD без HR, SDXL FP16 в STAGE_OFFLOAD."""
    res_15 = _run_full_chain(smoke_harness, "sd15", total_vram_mb=6144, total_ram_mb=16384, avail_ram_mb=10000)
    assert res_15["pre_free"]["always_offload"] is False

    res_fp8 = _run_full_chain(smoke_harness, "sdxl_fp8", total_vram_mb=6144, total_ram_mb=16384, avail_ram_mb=10000)
    assert res_fp8["plan"].enable_hr is False
    pol_fp8 = vram_policy.resolve_forge_offload_policy(
        width=res_fp8["plan"].width,
        height=res_fp8["plan"].height,
        architecture="sdxl",
        is_fp8=True,
        enable_adetailer=True,
        enable_hand_adetailer=True,
        enable_hr=False,
    )
    assert pol_fp8["mode"] in ("SMART_SWAP", "STAGE_OFFLOAD")

    res_fp16 = _run_full_chain(smoke_harness, "sdxl_fp16", total_vram_mb=6144, total_ram_mb=16384, avail_ram_mb=10000)
    assert res_fp16["pre_free"]["always_offload"] is True


def test_vram_profile_8gb_target_reference(smoke_harness):
    """8 ГБ VRAM (8152 МБ / 16065 МБ RAM): SD 1.5 и SDXL FP8 -> FAST_KEEP_VRAM; SDXL FP16 -> STAGE_OFFLOAD."""
    res_15 = _run_full_chain(smoke_harness, "sd15", total_vram_mb=8152, total_ram_mb=16065, avail_ram_mb=9500)
    assert res_15["pre_free"]["always_offload"] is False

    res_fp8 = _run_full_chain(smoke_harness, "sdxl_fp8", total_vram_mb=8152, total_ram_mb=16065, avail_ram_mb=9500)
    assert res_fp8["pre_free"]["always_offload"] is False
    assert res_fp8["pre_free"]["inference_memory_mb"] <= 768
    never_oom = (res_fp8["txt2img"].get("alwayson_scripts") or {}).get("Never OOM Integrated", {}).get("args")
    assert never_oom is None

    res_fp16 = _run_full_chain(smoke_harness, "sdxl_fp16", total_vram_mb=8152, total_ram_mb=16065, avail_ram_mb=9500)
    assert res_fp16["pre_free"]["always_offload"] is True


@pytest.mark.parametrize("model_key", ["sd15", "sdxl_fp8", "sdxl_fp16"])
def test_vram_profile_12gb_high_budget(smoke_harness, model_key):
    """12 ГБ VRAM (12288 МБ / 32768 МБ RAM): все 3 модели идут в FAST_KEEP_VRAM (always_offload=False)."""
    res = _run_full_chain(smoke_harness, model_key, total_vram_mb=12288, total_ram_mb=32768, avail_ram_mb=22000)
    assert res["out_file"].exists()
    assert res["pre_free"]["always_offload"] is False


# =============================================================================
# 2. Краевой случай дефицита системной ОЗУ: 4 ГБ RAM (Host RAM Starvation Guard)
# =============================================================================

def test_host_ram_starvation_4gb_blocks_pin_shared_memory(smoke_harness):
    """На 4 ГБ RAM (1200 МБ свободно) --pin-shared-memory категорически запрещён."""
    smoke_harness.configure_hardware(total_vram_mb=8152, total_ram_mb=4096, avail_ram_mb=1200)
    pm = ProcessManager()
    pm.is_port_open = MagicMock(side_effect=[False, True])
    with patch("services.asset_validator.AssetValidator.log_validation_summary"):
        pm._run_forge_start()

    cmd = smoke_harness.spy.popen_cmds[-1]
    assert "--pin-shared-memory" not in cmd


@pytest.mark.parametrize("vram_mb", [2048, 4096])
def test_host_ram_starvation_4gb_with_low_vram_heavy_sdxl(smoke_harness, vram_mb):
    """4 ГБ RAM (1200 МБ свободно) + 2/4 ГБ VRAM на SDXL FP16: защита от Host OOM."""
    res = _run_full_chain(smoke_harness, "sdxl_fp16", total_vram_mb=vram_mb, total_ram_mb=4096, avail_ram_mb=1200)

    assert "--pin-shared-memory" not in res["launch_cmd"]
    assert res["plan"].enable_hr is False
    assert res["pre_free"]["always_offload"] is True
    assert res["pre_free"].get("unload_models") is True, (
        "При переходе в STAGE_OFFLOAD в условиях дефицита RAM должен передаваться unload_models=True"
    )
    # Проверка защитного механизма при 1.2 ГБ свободной RAM на SDXL FP16
    ad_args = (res["txt2img"].get("alwayson_scripts") or {}).get("ADetailer", {}).get("args", [])
    ad_slots = [s for s in ad_args[2:] if isinstance(s, dict)]
    used_fp8_fallback = any(
        opt.get("forge_unet_storage_dtype") == "fp8e4" for opt in res["options"]
    ) or (res["txt2img"].get("override_settings") or {}).get("forge_unet_storage_dtype") == "fp8e4"
    assert used_fp8_fallback or len(ad_slots) <= 1, (
        "При критическом дефиците RAM (1200 MB) + 2/4 GB VRAM должен срабатывать защитный даунгрейд в fp8e4 или отключение лишних слотов ADetailer"
    )


# =============================================================================
# 3. Идемпотентность и последовательные переходы на 8 ГБ VRAM
# =============================================================================

def test_session_model_switching_and_arbiter_idempotency_8gb(smoke_harness):
    """SD 1.5 -> SDXL FP8 -> SDXL FP16 на 8 ГБ VRAM + идемпотентность VRAMArbiter."""
    monitor = smoke_harness.configure_hardware(total_vram_mb=8152, total_ram_mb=16065, avail_ram_mb=9500)

    mock_lm = MagicMock()
    mock_lm._is_model_unloaded = False
    mock_lm.process_manager = None
    mock_lm.unload_models_cli.side_effect = lambda: (setattr(mock_lm, "_is_model_unloaded", True) or True)

    fc = ForgeClient(system_monitor=monitor)
    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=fc)
    VRAMArbiter._instance = arbiter

    # Двойной вызов release_llm_for_diffusion -> ровно 1 реальная выгрузка LLM
    assert arbiter.release_llm_for_diffusion() is True
    assert arbiter.release_llm_for_diffusion() is True
    assert mock_lm.unload_models_cli.call_count == 1

    ad = ArtDirector()
    ad.system_monitor = monitor

    # Последовательный прогон 3 моделей в одной сессии
    for model_key in ("sd15", "sdxl_fp8", "sdxl_fp16"):
        spec = MODEL_SPECS[model_key]
        smoke_harness.configure_hardware(
            total_vram_mb=8152, total_ram_mb=16065, avail_ram_mb=9500, sdxl_fp8=spec["fp8"]
        )
        plan = ad.plan_from_visual_intent(
            visual_intent=VisualIntent(prompt="1girl, portrait, hands", suggested_model=spec["id"]),
            preferred_style=spec["id"],
            available_models=smoke_harness.spy.models_list,
        )
        fc.text_to_image(**plan.to_forge_kwargs(output_dir=smoke_harness.outputs_dir))

    policy_calls = [c for c in smoke_harness.spy.free_vram_calls if "always_offload" in c]
    assert len(policy_calls) >= 2
    # SD 1.5 -> FAST_KEEP_VRAM (False, inf=768), SDXL FP8 (896x1152) -> FAST_KEEP_VRAM (False, inf=768, дедупликация free_vram), SDXL FP16 -> STAGE_OFFLOAD (True, unload_models=True)
    assert policy_calls[0]["always_offload"] is False
    assert policy_calls[0]["inference_memory_mb"] == 768
    assert policy_calls[-1]["always_offload"] is True
    assert policy_calls[-1]["unload_models"] is True

    # После text_to_image _checkpoint_unloaded=True -> переход обратно к LLM не шлёт повторный unload-checkpoint
    unload_count_before_llm = len(smoke_harness.spy.unload_calls)
    assert fc._checkpoint_unloaded is True
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == GPUOwner.LLM
    assert len(smoke_harness.spy.unload_calls) == unload_count_before_llm


def test_forge_startup_flags_blackwell_ada_16gb_ram():
    """Сценарий 2: Профиль Forge на Blackwell/Ada compute_cap >= (8, 0) и 16 ГБ RAM.
    resolve_forge_startup_flags(llm_active=True, ...) на compute_cap = (12, 0) (RTX 5050) и total_ram_gb = 15.7
    формирует флаг --vae-in-bf16 и не включает --no-half-vae, --vae-in-fp32 и --pin-shared-memory.
    """
    hw_blackwell = {
        "vendor": "nvidia",
        "compute_cap": (12, 0),
        "vram_total_mb": 8151,
        "gpu_name": "NVIDIA GeForce RTX 5050 Laptop GPU"
    }
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=hw_blackwell), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):

        # 1. При llm_active=True на 15.7 ГБ RAM:
        flags, meta = vram_policy.resolve_forge_startup_flags(
            llm_active=True,
            total_ram_gb=15.7,
            avail_ram_mb=7868.0,
        )

        assert "--vae-in-bf16" in flags
        assert "--no-half-vae" not in flags
        assert "--vae-in-fp32" not in flags
        assert "--pin-shared-memory" not in flags
        assert meta["pin_shared_memory"] is False
        assert meta["vae_precision"] == "bf16"
        assert "активной LLM" in meta["pin_reason"]

        # 2. При llm_active=False, но RAM < 9000 MB: pin-shared-memory также выключен
        flags_idle, meta_idle = vram_policy.resolve_forge_startup_flags(
            llm_active=False,
            total_ram_gb=15.7,
            avail_ram_mb=7868.0,
        )
        assert "--pin-shared-memory" not in flags_idle
        assert meta_idle["pin_shared_memory"] is False
        assert "< 9000MB" in meta_idle["pin_reason"]


def test_session_sequential_sdxl_model_switching_with_ram_cache_guard_16gb():
    """Сценарий 3: Последовательная смена двух SDXL-чекпоинтов в ForgeClient на 16 ГБ RAM.
    set_model(ckpt_A) -> unload_checkpoint() -> set_model(ckpt_B) -> unload_checkpoint() при total_ram <= 16.5 GB:
    при каждом переключении в /sdapi/v1/options передаются sd_checkpoint_cache == 0 и sd_checkpoints_limit == 1,
    а при выгрузке вызывается принудительная очистка free_vram(force=True, unload_models=True).
    """
    fc = ForgeClient()
    options_calls = []
    free_vram_calls = []

    def mock_post(url, *args, **kwargs):
        resp = MagicMock(status_code=200)
        if "options" in url:
            options_calls.append(kwargs.get("json", {}))
        elif "free-vram" in url:
            free_vram_calls.append(kwargs.get("json", {}))
        return resp

    fake_vm = MagicMock(total=15.7 * (1024 ** 3), available=6000 * (1024 ** 2))

    with patch("psutil.virtual_memory", return_value=fake_vm), \
         patch("requests.post", side_effect=mock_post), \
         patch.object(fc, "get_models", return_value=[
             {"title": "sdxl_alpha.safetensors"},
             {"title": "sdxl_beta.safetensors"}
         ]), \
         patch.object(fc, "get_current_model", side_effect=["old.safetensors", "sdxl_alpha.safetensors"]), \
         patch.object(fc, "_wait_for_vram_clear"):

        # 1. Загрузка первого SDXL чекпоинта
        ok_a = fc.set_model("sdxl_alpha.safetensors")
        assert ok_a is True
        assert len(options_calls) == 1
        assert options_calls[0]["sd_model_checkpoint"] == "sdxl_alpha.safetensors"
        assert options_calls[0]["sd_checkpoint_cache"] == 0
        assert options_calls[0]["sd_checkpoints_limit"] == 1
        assert options_calls[0]["forge_unet_storage_dtype"] == "fp8e4"

        # 2. Выгрузка первого чекпоинта
        unloaded_a = fc.unload_checkpoint()
        assert unloaded_a is True
        assert len(free_vram_calls) == 1
        assert free_vram_calls[0]["unload_models"] is True

        # 3. Загрузка второго SDXL чекпоинта
        ok_b = fc.set_model("sdxl_beta.safetensors")
        assert ok_b is True
        assert len(options_calls) == 2
        assert options_calls[1]["sd_model_checkpoint"] == "sdxl_beta.safetensors"
        assert options_calls[1]["sd_checkpoint_cache"] == 0
        assert options_calls[1]["sd_checkpoints_limit"] == 1
        assert options_calls[1]["forge_unet_storage_dtype"] == "fp8e4"

        # 4. Выгрузка второго чекпоинта
        unloaded_b = fc.unload_checkpoint()
        assert unloaded_b is True
        assert len(free_vram_calls) == 2
        assert free_vram_calls[1]["unload_models"] is True


def test_forge_vae_tiling_isolated_contract_and_options_emulation(tmp_path):
    """Изолированный смоук-тест контракта VAE tiling и эмуляции Options.set() Forge.

    Проверяет:
    1. Формирование payload при want_vae_tiling=True:
       - alwayson_scripts["Never OOM Integrated"]["args"] == [False, True]
       - Полное отсутствие 'forge_vae_tiling' и 'sd_vae_tile' как в корне payload, так и в override_settings.
       - Эмуляция Forge Options.set() для каждого ключа override_settings гарантированно НЕ вызывает KeyError.
       - fc._never_oom_vae_active переходит в True.
    2. Сброс состояния при последующем вызове с want_vae_tiling=False:
       - alwayson_scripts["Never OOM Integrated"]["args"] == [False, False] (сброс в Forge)
       - fc._never_oom_vae_active сбрасывается в False (состояние не залипает).
       - Ключи override_settings также валидны для Options.set().
    3. Полная изоляция: все файлы сохраняются в tmp_path, реальная сеть не используется, мусор не остаётся.
    """
    class SimulatedForgeOptions:
        """Эмулятор modules/options.py из SD WebUI Forge с проверкой по data_labels."""
        def __init__(self):
            # Реалистичный набор зарегистрированных опций Forge
            self.data_labels = {
                "sd_model_checkpoint": {},
                "sd_vae": {},
                "forge_unet_storage_dtype": {},
                "forge_inference_memory": {},
                "forge_async_loading": {},
                "forge_pin_shared_memory": {},
                "sd_checkpoint_cache": {},
                "sd_checkpoints_limit": {},
                "CLIP_stop_at_last_layers": {},
            }
            self.values = {}

        def set(self, key, value):
            if key not in self.data_labels:
                raise KeyError(key)
            self.values[key] = value

    forge_opts = SimulatedForgeOptions()
    # Проверяем, что эмулятор действительно ловит старые ошибочные ключи
    with pytest.raises(KeyError):
        forge_opts.set("forge_vae_tiling", True)
    with pytest.raises(KeyError):
        forge_opts.set("sd_vae_tile", True)

    captured_payloads = []

    def mock_post_handler(url, json_body=None, data=None, **kwargs):
        payload_data = json_body if json_body is not None else kwargs.get("json")
        if payload_data is None and isinstance(data, str):
            payload_data = json.loads(data)
        elif payload_data is None:
            payload_data = {}
        captured_payloads.append(payload_data)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "images": [VALID_PNG_B64],
            "parameters": payload_data,
            "info": "{}",
        }
        resp.text = json.dumps(resp.json.return_value)
        return resp

    fc = ForgeClient(base_url="http://127.0.0.1:7860")
    fc._cached_scripts = {"txt2img": ["never oom integrated"], "img2img": []}
    assert fc._never_oom_vae_active is False

    with patch("requests.Session.post", side_effect=mock_post_handler), \
         patch.object(fc, "is_server_ready", return_value=True):
        # ---------------------------------------------------------------------
        # Сценарий 1: Генерация с want_vae_tiling = True (например, профиль 2 ГБ)
        # ---------------------------------------------------------------------
        res_1 = fc.text_to_image(
            prompt="cyberpunk neon road",
            output_dir=tmp_path,
            want_vae_tiling=True,
            width=512,
            height=512,
            steps=20,
        )
        assert res_1.exists()
        assert res_1.parent == tmp_path
        assert fc._never_oom_vae_active is True

        p1 = captured_payloads[0]
        # Проверяем контракт: VAE tiling передан через Never OOM Integrated
        scripts_1 = p1.get("alwayson_scripts", {})
        assert "Never OOM Integrated" in scripts_1
        assert scripts_1["Never OOM Integrated"]["args"] == [False, True]

        # Проверяем отсутствие недопустимых ключей в корне payload
        assert "forge_vae_tiling" not in p1
        assert "sd_vae_tile" not in p1

        # Проверяем override_settings и эмулируем Forge Options.set()
        override_1 = p1.get("override_settings", {})
        assert "forge_vae_tiling" not in override_1
        assert "sd_vae_tile" not in override_1

        # Каждый ключ из override_settings обязан успешно обрабатываться Forge без KeyError
        for k, v in override_1.items():
            forge_opts.set(k, v)

        # ---------------------------------------------------------------------
        # Сценарий 2: Следующая генерация с want_vae_tiling = False (сброс состояния)
        # ---------------------------------------------------------------------
        res_2 = fc.text_to_image(
            prompt="cyberpunk neon road daylight",
            output_dir=tmp_path,
            want_vae_tiling=False,
            width=512,
            height=512,
            steps=20,
        )
        assert res_2.exists()
        assert res_2.parent == tmp_path
        # Флаг клиента сбросился
        assert fc._never_oom_vae_active is False

        p2 = captured_payloads[1]
        scripts_2 = p2.get("alwayson_scripts", {})
        # Для сброса серверу Forge обязательно переданы аргументы [False, False]
        assert "Never OOM Integrated" in scripts_2
        assert scripts_2["Never OOM Integrated"]["args"] == [False, False]

        # Ключи отсутствуют
        assert "forge_vae_tiling" not in p2
        assert "sd_vae_tile" not in p2
        override_2 = p2.get("override_settings", {})
        assert "forge_vae_tiling" not in override_2
        assert "sd_vae_tile" not in override_2

        for k, v in override_2.items():
            forge_opts.set(k, v)

        # ---------------------------------------------------------------------
        # Сценарий 3: Повторная генерация с want_vae_tiling = False (уже выключено)
        # ---------------------------------------------------------------------
        res_3 = fc.text_to_image(
            prompt="cyberpunk sunset",
            output_dir=tmp_path,
            want_vae_tiling=False,
            width=512,
            height=512,
            steps=20,
        )
        assert res_3.exists()
        assert res_3.parent == tmp_path
        assert fc._never_oom_vae_active is False

        p3 = captured_payloads[2]
        assert "forge_vae_tiling" not in p3
        assert "sd_vae_tile" not in p3
        override_3 = p3.get("override_settings", {})
        assert "forge_vae_tiling" not in override_3
        assert "sd_vae_tile" not in override_3

        for k, v in override_3.items():
            forge_opts.set(k, v)


def test_hires_scale_adaptive_upscale_and_downscale_smoke():
    """Смок-тест аккуратного повышения и понижения Hires.fix в vram_policy.resolve_safe_hires_scale.

    Проверяет:
    1. На 8 ГБ VRAM (Pure Compute) при запасе памяти SD 1.5 (1.25x -> 1.50x) и SDXL FP8 (1.25x -> 1.35x)
       аккуратно повышаются строго в пределах FAST_KEEP_VRAM.
    2. При выключенном Hires (1.0x) или SDXL FP16 на 8 ГБ повышение не срабатывает.
    3. При дефиците VRAM (например, 4.5 ГБ для SD 1.5) масштаб аккуратно снижается ниже запрошенного (1.50x -> < 1.50x)
       без перехода в STAGE_OFFLOAD.
    """
    vram_policy._hardware_budget_cache = None

    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 150)):
        # 1a. SD 1.5: аккуратное повышение 1.25x -> 1.50x в FAST_KEEP_VRAM
        res_sd15_up = vram_policy.resolve_safe_hires_scale(
            width=512,
            height=768,
            requested_hr_scale=1.25,
            architecture="sd15",
            is_fp8=False,
            enable_adetailer=True,
            enable_hand_adetailer=True,
            active_lora_count=1,
            hr_upscaler="4x-UltraSharp",
            vae_tiling=False,
        )
        assert res_sd15_up["allowed"] is True
        assert res_sd15_up["effective_scale"] == 1.5
        assert (res_sd15_up["hr_width"], res_sd15_up["hr_height"]) == (768, 1152)
        assert res_sd15_up["policy"]["mode"] == "FAST_KEEP_VRAM"
        assert "повышено под запас VRAM" in res_sd15_up["reason"]

        # 1b. SDXL FP8: аккуратное повышение 1.25x -> 1.35x в FAST_KEEP_VRAM
        res_sdxl_up = vram_policy.resolve_safe_hires_scale(
            width=896,
            height=1152,
            requested_hr_scale=1.25,
            architecture="sdxl",
            is_fp8=True,
            enable_adetailer=True,
            enable_hand_adetailer=True,
            active_lora_count=0,
            hr_upscaler="Latent",
            vae_tiling=True,
        )
        assert res_sdxl_up["allowed"] is True
        assert res_sdxl_up["effective_scale"] == 1.35
        assert res_sdxl_up["policy"]["mode"] == "FAST_KEEP_VRAM"
        assert "повышено под запас VRAM" in res_sdxl_up["reason"]

        # 2. Выключенный Hires и SDXL FP16 не повышаются
        res_off = vram_policy.resolve_safe_hires_scale(
            width=512,
            height=768,
            requested_hr_scale=1.0,
            architecture="sd15",
            is_fp8=False,
        )
        assert res_off["allowed"] is False
        assert res_off["effective_scale"] == 1.0

        res_fp16 = vram_policy.resolve_safe_hires_scale(
            width=896,
            height=1152,
            requested_hr_scale=1.25,
            architecture="sdxl",
            is_fp8=False,
        )
        assert res_fp16["allowed"] is False

    # 3. Аккуратное понижение при ограниченной VRAM (4.5 ГБ, Primary Display 700 МБ)
    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(4.5, 700)):
        res_down = vram_policy.resolve_safe_hires_scale(
            width=768,
            height=512,
            requested_hr_scale=1.50,
            architecture="sd15",
            is_fp8=False,
            enable_adetailer=False,
            hr_upscaler="Latent",
            vae_tiling=False,
        )
        assert res_down["allowed"] is True
        assert 1.15 <= res_down["effective_scale"] < 1.50
        assert res_down["policy"]["mode"] in ("FAST_KEEP_VRAM", "SMART_SWAP")
        assert "подогнано под бюджет VRAM" in res_down["reason"]


