"""
tests/smoke/test_user_personas_journey_smoke.py
================================================
Сквозной (E2E) смоук-сьют NeonRoute: моделирование полного пути пользователей
с различными аппаратными конфигурациями из README.md:

1. Сценарий «AMD RX 570 (4 ГБ VRAM) + Intel Xeon E5-2670 v2 (без AVX2) + 16 ГБ RAM»:
   - Детекция оборудования: non-NVIDIA (AMD), отсутствие AVX2 на CPU Xeon.
   - Выбор и конфигурация llama-server: Vulkan / CPU fallback без AVX2/CUDA.
   - Безопасный расчет оффлоада в LayerBudgeter без nvidia-smi / pynvml.
   - Блокировка автоустановки SD WebUI Forge (supported=False для AMD).

2. Сценарий «NVIDIA GTX 1050 Ti (4 ГБ VRAM, Compute Cap 6.1) + Ryzen 5 1600 + 16 ГБ RAM + Непатченный Forge»:
   - Детекция оборудования: NVIDIA Pascal CC 6.1, 4 ГБ VRAM.
   - Выбор CUDA llama-server с валидацией целостности DLL (cudart64 / cublas64).
   - Установка Forge в tmp_path, создание forge_launcher.bat и привязка DreamShaper 8.
   - Проверка флагов устаревшей архитектуры (--no-half-vae, --vae-in-fp32, --always-offload-from-vram).
   - Ветка «Непатченный Forge»: обработка HTTP 404 на /sdapi/v1/free-vram без вылета приложения.

3. Сценарий «NVIDIA RTX 5050 Laptop (8 ГБ VRAM, Blackwell CC 12.0) + Полный цикл до результата»:
   - Детекция оборудования: Blackwell CC 12.0, 8 ГБ VRAM, 16 ГБ RAM.
   - Профиль Forge: --vae-in-bf16 без --no-half-vae и без принудительного стартового оффлоада.
   - Запуск LLM с расчетом слоев LayerBudgeter и параметром отключения mmap (--load-mode none / --no-mmap).
   - Полный цикл инференса и арбитража памяти:
     LLM (генерация текста) -> VRAMArbiter -> Forge (генерация арта с ADetailer) -> VRAMArbiter -> возврат LLM.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch
import base64
import io
import pytest
import requests
from PIL import Image

import config
from utils.system_monitor import SystemMonitor
from core.forge_locator import find_forge_root, update_server_config_forge_root
from services.layer_budgeter import LayerBudgeter
from services.llama_downloader import (
    resolve_required_build,
    select_download_urls,
    is_llama_server_installed,
)
from services.server_config import ServerConfigResolver
from services.forge_downloader import (
    resolve_forge_hardware_spec,
    create_forge_launcher_bat,
)
from services.forge_patcher import ForgePatcher, ForgePatchStatus
from services.forge_client import ForgeClient
from services.art_director import vram_policy
from services.art_director.contracts import VisualIntent
from services.art_director.director import ArtDirector
from services.vram_arbiter import VRAMArbiter, GPUOwner
from services.art_director_config import load_art_director_models, save_art_director_models

try:
    assert_diag
except NameError:
    from tests.smoke.conftest import assert_diag, assert_diag_eq, assert_diag_in


def _make_png_b64() -> str:
    """Генерирует минимальный валидный PNG (64x64) в формате Base64 для тестов инференса."""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(16, 24, 32)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


SAMPLE_GITHUB_RELEASE_ASSETS = [
    {
        "name": "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "browser_download_url": "https://github.com/mock/cudart-12.4.zip",
    },
    {
        "name": "llama-b12426-bin-win-cuda-12.4-x64.zip",
        "browser_download_url": "https://github.com/mock/cuda-12.4.zip",
    },
    {
        "name": "llama-b12426-bin-win-vulkan-x64.zip",
        "browser_download_url": "https://github.com/mock/vulkan.zip",
    },
    {
        "name": "llama-b12426-bin-win-avx2-x64.zip",
        "browser_download_url": "https://github.com/mock/avx2.zip",
    },
    {
        "name": "llama-b12426-bin-win-noavx-x64.zip",
        "browser_download_url": "https://github.com/mock/noavx.zip",
    },
    {
        "name": "llama-b12426-bin-win-cpu-x64.zip",
        "browser_download_url": "https://github.com/mock/cpu.zip",
    },
]


STOCK_API_PY = '''
class Api:
    def __init__(self):
        self.add_api_route("/sdapi/v1/unload-checkpoint", self.unloadapi, methods=["POST"])
        self.add_api_route("/sdapi/v1/reload-checkpoint", self.reloadapi, methods=["POST"])

    def unloadapi(self):
        sd_models.unload_model_weights()
        return {}

    def reloadapi(self):
        sd_models.send_model_to_device(shared.sd_model)
        return {}

    def set_config(self, req: dict[str, Any]):
        for k, v in req.items():
            shared.opts.set(k, v, is_api=True)
        shared.opts.save(shared.config_filename)
        return

    def get_sd_models(self):
        return [{"title": x.title, "model_name": x.model_name, "hash": x.shorthash, "sha256": x.sha256, "filename": x.filename} for x in sd_models.checkpoints_list.values()]
'''

STOCK_MODELS_PY = '''
class SDModelItem(BaseModel):
    title: str = Field(title="Title")
    model_name: str = Field(title="Model name")
    hash: Optional[str] = Field(title="Short hash")
    sha256: Optional[str] = Field(title="sha256 hash")
    filename: str = Field(title="Filename")
    config: Optional[str] = Field(title="Config file")
'''

STOCK_SD_MODELS_PY = '''
def reload_model_weights(sd_model=None, info=None, forced_reload=False):
    pass

def unload_model_weights(sd_model=None, info=None):
    pass

def apply_token_merging(sd_model, token_merging_ratio):
    pass
'''


def _create_unpatched_synthetic_forge(root_dir: Path) -> Path:
    """Вспомогательный генератор изолированной структуры Forge без патча NeonRoute."""
    webui_dir = root_dir / "webui"
    api_dir = webui_dir / "modules" / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (root_dir / "run.bat").write_text("@echo off", encoding="utf-8")
    (root_dir / "webui-user.bat").write_text("@echo off\nset COMMANDLINE_ARGS=", encoding="utf-8")
    (webui_dir / "launch.py").write_text("# mock launch", encoding="utf-8")

    (api_dir / "api.py").write_text(STOCK_API_PY, encoding="utf-8")
    (api_dir / "models.py").write_text(STOCK_MODELS_PY, encoding="utf-8")
    (webui_dir / "modules" / "sd_models.py").write_text(STOCK_SD_MODELS_PY, encoding="utf-8")
    (webui_dir / "models" / "Stable-diffusion").mkdir(parents=True, exist_ok=True)
    (webui_dir / "models" / "adetailer").mkdir(parents=True, exist_ok=True)
    return root_dir


# =========================================================================
# СЦЕНАРИЙ 1: AMD RX 570 + INTEL XEON E5-2670 v2 (БЕЗ AVX2)
# =========================================================================

def test_user_journey_amd_rx570_xeon_no_avx2(smoke_isolation_guard, tmp_path, monkeypatch):
    """
    Сквозной пользовательский путь:
    Железо: AMD Radeon RX 570 (4 ГБ VRAM) + Intel Xeon E5-2670 v2 (без AVX2) + 16 ГБ RAM.
    1. Детекция: non-NVIDIA (AMD), отсутствие AVX2 на уровне процессора.
    2. Выбор движка llama-server: Vulkan / CPU fallback без выбора AVX2 или CUDA ассетов.
    3. Расчет оффлоада в LayerBudgeter: безопасное определение NGL без сбоев из-за отсутствия NVML.
    4. Попытка установки Forge: блокировка (supported=False) с понятным объяснением.
    """
    scen = "Сценарий 1: AMD RX 570 + Xeon (без AVX2)"

    # 1. Мокирование детекции оборудования
    hw_profile_amd = {
        "supported": False,
        "gpu_name": "Radeon RX 570 Series",
        "vendor": "amd",
        "vram_total_mb": 4096,
        "compute_cap": "N/A",
        "recommended_kv": "f16",
        "recommended_fa": False,
        "cpu_avx2": False,
    }
    monkeypatch.setattr(LayerBudgeter, "has_cpu_avx2", lambda: False)
    monkeypatch.setattr(LayerBudgeter, "detect_hardware_kv_profile", classmethod(lambda cls: hw_profile_amd))
    monkeypatch.setattr("utils.system_monitor.HAS_PYNVML", False)

    profile = LayerBudgeter.detect_hardware_kv_profile()
    assert_diag(profile["vendor"] == "amd", scen, "Детекция вендора GPU", "amd", profile["vendor"])
    assert_diag(profile["cpu_avx2"] is False, scen, "Флаг поддержки AVX2 процессором", False, profile["cpu_avx2"])
    assert_diag(profile["vram_total_mb"] == 4096, scen, "Объем VRAM AMD GPU", 4096, profile["vram_total_mb"])

    # 2. Логика выбора сборки llama-server
    target_build = resolve_required_build(profile["vendor"])
    assert_diag(target_build == "vulkan", scen, "Выбор типа сборки llama-server для AMD", "vulkan", target_build)

    # Проверка выбора URL для Vulkan при отсутствии AVX2
    vulkan_urls = select_download_urls(SAMPLE_GITHUB_RELEASE_ASSETS, target_build, cpu_avx2=False)
    vulkan_asset_names = [name for name, _ in vulkan_urls]
    assert_diag(
        "llama-b12426-bin-win-vulkan-x64.zip" in vulkan_asset_names,
        scen,
        "Выбор Vulkan-архива",
        "llama-b12426-bin-win-vulkan-x64.zip",
        vulkan_asset_names
    )
    assert_diag(
        not any("cuda" in name.lower() for name in vulkan_asset_names),
        scen,
        "Исключение CUDA-сборок для AMD",
        "Отсутствие cuda в именах",
        vulkan_asset_names
    )

    # Проверка CPU-fallback: если Vulkan недоступен, выбирается сборка без AVX2 (noavx)
    cpu_urls = select_download_urls(SAMPLE_GITHUB_RELEASE_ASSETS, "cpu", cpu_avx2=False)
    cpu_asset_names = [name for name, _ in cpu_urls]
    assert_diag(
        "llama-b12426-bin-win-noavx-x64.zip" in cpu_asset_names,
        scen,
        "CPU-fallback без AVX2 выбирает noavx",
        "llama-b12426-bin-win-noavx-x64.zip",
        cpu_asset_names
    )
    assert_diag(
        not any("avx2" in name.lower() for name in cpu_asset_names),
        scen,
        "Исключение AVX2-сборок для процессора Xeon v2",
        "Отсутствие avx2 в именах",
        cpu_asset_names
    )

    # 3. Расчет оффлоада в LayerBudgeter без NVML/CUDA
    fake_gguf = tmp_path / "qwen2.5-7b.gguf"
    fake_gguf.write_bytes(b"GGUF" + b"\x00" * 4096)
    mock_meta = {
        "model_size_mb": 4200.0,
        "total_layers": 28,
        "vocab_size": 152064,
    }
    with patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):
        ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
            model_path=str(fake_gguf),
            target_context=4096,
            total_vram_mb=4096,
            available_ram_mb=16384,
        )
        assert_diag(isinstance(ngl, int) and ngl >= 0, scen, "Безопасный NGL для AMD/CPU", "Целое неотрицательное число", ngl)
        assert_diag(can_run is True, scen, "Модель помещается в 16 ГБ RAM (гибрид/CPU)", True, can_run)

    # 4. Попытка установки Forge на системе без NVIDIA GPU
    forge_spec = resolve_forge_hardware_spec()
    assert_diag(
        forge_spec.supported is False,
        scen,
        "Блокировка установки Forge для AMD GPU",
        False,
        forge_spec.supported
    )
    assert_diag(
        forge_spec.is_blackwell is False,
        scen,
        "Blackwell-флаг для AMD",
        False,
        forge_spec.is_blackwell
    )
    assert_diag(
        "Radeon RX 570" in forge_spec.gpu_summary,
        scen,
        "Отображение имени GPU в сводке Forge",
        "Содержит 'Radeon RX 570'",
        forge_spec.gpu_summary
    )


# =========================================================================
# СЦЕНАРИЙ 2: NVIDIA GTX 1050 Ti + RYZEN 5 1600 + НЕПАТЧЕННЫЙ FORGE
# =========================================================================

def test_user_journey_gtx1050ti_legacy_unpatched_forge(smoke_isolation_guard, tmp_path, monkeypatch):
    """
    Сквозной пользовательский путь:
    Железо: NVIDIA GTX 1050 Ti (4 ГБ VRAM, Compute Cap 6.1) + Ryzen 5 1600 + 16 ГБ RAM.
    1. Детекция: NVIDIA Pascal (CC 6.1 < 7.5), 4 ГБ VRAM.
    2. Выбор движка: выбор CUDA-сборки и проверка целостности библиотек (cudart64 / cublas64).
    3. Автоустановка Forge в tmp_path: создание forge_launcher.bat, регистрация DreamShaper 8
       И валидация флагов устаревшей архитектуры (--no-half-vae, --vae-in-fp32, --always-offload-from-vram).
    4. Ветка «Пользователь не применил патч»:
       - ForgePatcher фиксирует статус NEEDS_PATCH.
       - Вызов /sdapi/v1/free-vram возвращает HTTP 404 (эндпоинт отсутствует в оригинале).
       - ForgeClient не падает с необработанным исключением, корректно фиксирует _free_vram_supported=False
         и безопасно возвращает False.
    """
    scen = "Сценарий 2: GTX 1050 Ti + Непатченный Forge"

    # 1. Мокирование детекции оборудования Pascal
    hw_profile_gtx1050 = {
        "supported": False,  # Pascal CC 6.1 < 7.5 (Turing)
        "gpu_name": "NVIDIA GeForce GTX 1050 Ti",
        "vendor": "nvidia",
        "vram_total_mb": 4096,
        "compute_cap": "6.1",
        "recommended_kv": "f16",
        "recommended_fa": False,
        "cpu_avx2": True,
    }
    monkeypatch.setattr(LayerBudgeter, "has_cpu_avx2", lambda: True)
    monkeypatch.setattr(LayerBudgeter, "detect_hardware_kv_profile", classmethod(lambda cls: hw_profile_gtx1050))
    monkeypatch.setattr("services.art_director.vram_policy.resolve_hardware_vram_budget", lambda monitor=None: (4.0, 400))

    # 2. Выбор CUDA-сборки и проверка зависимостей DLL
    build_type = resolve_required_build("nvidia")
    assert_diag(build_type == "cuda", scen, "Тип сборки llama-server для NVIDIA", "cuda", build_type)

    cuda_urls = select_download_urls(SAMPLE_GITHUB_RELEASE_ASSETS, "cuda", cpu_avx2=True)
    cuda_names = [name for name, _ in cuda_urls]
    assert_diag(
        "llama-b12426-bin-win-cuda-12.4-x64.zip" in cuda_names and "cudart-llama-bin-win-cuda-12.4-x64.zip" in cuda_names,
        scen,
        "Выбор CUDA-бинарника и рантайма cudart",
        "Оба zip-архива присутствуют",
        cuda_names
    )

    # Проверка целостности CUDA DLL в is_llama_server_installed
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe_file = bin_dir / "llama-server.exe"
    exe_file.write_bytes(b"MZ" + b"\x00" * 2048)
    cuda_dll = bin_dir / "ggml-cuda.dll"
    cuda_dll.write_bytes(b"CUDA" + b"\x00" * 1024)

    # Без cudart/cublas установка не считается завершенной
    assert_diag(
        is_llama_server_installed(bin_dir) is False,
        scen,
        "Проверка отсутствия обязательных CUDA DLL",
        False,
        is_llama_server_installed(bin_dir)
    )

    # Добавляем необходимые библиотеки рантайма
    (bin_dir / "cudart64_12.dll").write_bytes(b"CUDART" + b"\x00" * 1024)
    (bin_dir / "cublas64_12.dll").write_bytes(b"CUBLAS" + b"\x00" * 1024)
    assert_diag(
        is_llama_server_installed(bin_dir) is True,
        scen,
        "Проверка завершенности установки CUDA после добавления DLL",
        True,
        is_llama_server_installed(bin_dir)
    )

    # 3. Установка Forge и проверка лаунчера со стартовыми флагами устаревшей архитектуры
    forge_dir = tmp_path / "forge_gtx1050ti"
    _create_unpatched_synthetic_forge(forge_dir)

    # Создание forge_launcher.bat через инсталлятор
    launcher_path = create_forge_launcher_bat(forge_dir)
    assert_diag(launcher_path.is_file(), scen, "Создание forge_launcher.bat", True, launcher_path.is_file())

    # Проверка обнаружения и выбора лаунчера через боевой ForgeLocator
    detected_root = find_forge_root(configured_path=forge_dir)
    assert_diag(
        detected_root == forge_dir.resolve(),
        scen,
        "Детекция рабочей директории find_forge_root",
        forge_dir.resolve(),
        detected_root
    )

    update_ok = update_server_config_forge_root(forge_dir)
    assert_diag(update_ok is True, scen, "Обновление конфигурации боевым локатором", True, update_ok)
    assert_diag_eq(
        config.FORGE_LAUNCHER_PATH.name,
        "forge_launcher.bat",
        scen,
        "Приоритет выбора лаунчера боевым локатором (config.FORGE_LAUNCHER_PATH)"
    )

    # Регистрация стартового чекпоинта DreamShaper 8
    sd_dir = forge_dir / "webui" / "models" / "Stable-diffusion"
    ckpt_file = sd_dir / "dreamshaper_8.safetensors"
    ckpt_file.write_bytes(b"DREAMSHAPER_MOCK")
    models = load_art_director_models()
    models.append({
        "id": "fantasy_art",
        "name": "DreamShaper 8",
        "checkpoint": "dreamshaper_8.safetensors",
        "architecture": "sd15",
        "is_default": True,
        "active": True,
    })
    save_art_director_models(models)
    reloaded_models = load_art_director_models()
    assert_diag(
        any(m.get("checkpoint") == "dreamshaper_8.safetensors" and m.get("is_default") for m in reloaded_models),
        scen,
        "Регистрация и активация DreamShaper 8 в конфигурации",
        True,
        True
    )

    # Проверка стартовых флагов для GPU Pascal 4 ГБ:
    # 4 ГБ (< 6 ГБ) -> --always-offload-from-vram
    # CC 6.1 (< 8.0) -> --no-half-vae и --vae-in-fp32, строго без --vae-in-bf16
    flags, meta = vram_policy.resolve_forge_startup_flags(
        total_ram_gb=16.0,
        avail_ram_mb=12000.0,
    )
    assert_diag("--no-half-vae" in flags, scen, "Флаг --no-half-vae для CC 6.1", True, "--no-half-vae" in flags)
    assert_diag("--vae-in-fp32" in flags, scen, "Флаг --vae-in-fp32 для CC 6.1", True, "--vae-in-fp32" in flags)
    assert_diag("--always-offload-from-vram" in flags, scen, "Флаг --always-offload-from-vram для 4 ГБ VRAM", True, "--always-offload-from-vram" in flags)
    assert_diag("--vae-in-bf16" not in flags, scen, "Отсутствие --vae-in-bf16 на Pascal", True, "--vae-in-bf16" not in flags)

    # 4. Ветка «Пользователь не применил патч Forge»
    patch_status, patch_msg = ForgePatcher.check_status(forge_dir)
    assert_diag_eq(patch_status, ForgePatchStatus.NEEDS_PATCH, scen, "Статус проверки непатченного Forge")

    # Имитируем запрос к оригинальному серверу Forge без эндпоинта /sdapi/v1/free-vram (HTTP 404)
    def mock_requests_post(url, **kwargs):
        resp = MagicMock()
        if "/sdapi/v1/free-vram" in url:
            resp.status_code = 404
            resp.text = "Not Found"
            return resp
        elif "/sdapi/v1/unload-checkpoint" in url:
            resp.status_code = 200
            resp.json.return_value = {}
            return resp
        resp.status_code = 200
        return resp

    monkeypatch.setattr(requests, "post", mock_requests_post)

    client = ForgeClient(base_url="http://127.0.0.1:7860")
    assert_diag(getattr(client, "_free_vram_supported", True) is True, scen, "Исходный флаг поддержки free-vram", True, client._free_vram_supported)

    # Вызов free_vram при 404 не должен приводить к падению
    try:
        res = client.free_vram(unload_models=True)
    except Exception as exc:
        pytest.fail(f"[{scen}] free_vram упал с необработанным исключением при HTTP 404: {exc}")

    assert_diag(res is False, scen, "Результат вызова free-vram при 404", False, res)
    assert_diag(
        client._free_vram_supported is False,
        scen,
        "Фиксация деградации режима при отсутствии патча",
        False,
        client._free_vram_supported
    )

    # Повторный вызов не должен производить сетевой запрос
    with patch("requests.post", side_effect=RuntimeError("Unexpected network request on disabled free-vram")):
        second_call = client.free_vram(unload_models=True)
        assert_diag(second_call is False, scen, "Быстрый возврат без сетевого вызова", False, second_call)


# =========================================================================
# СЦЕНАРИЙ 3: NVIDIA RTX 5050 LAPTOP (BLACKWELL CC 12.0) - ПОЛНЫЙ ЦИКЛ
# =========================================================================

def test_user_journey_rtx5050_blackwell_full_cycle(smoke_isolation_guard, tmp_path, monkeypatch):
    """
    Сквозной пользовательский путь:
    Железо: NVIDIA GeForce RTX 5050 Laptop GPU (8 ГБ VRAM, Blackwell CC 12.0) + 16 ГБ RAM.
    1. Детекция: Blackwell CC 12.0, 8 ГБ VRAM, рекомендация KV q8_0 и Flash Attention.
    2. Стартовые флаги Forge: --vae-in-bf16 без --no-half-vae и без стартового --always-offload-from-vram.
    3. Запуск LLM: расчет слоев в LayerBudgeter, аргумент отключения mmap (--load-mode none / --no-mmap).
    4. Сквозной цикл инференса:
       - LLM генерирует текстовый ответ (владелец GPUOwner.LLM).
       - Переключение VRAMArbiter на задачу диффузии (владелец GPUOwner.DIFFUSION, выгрузка LLM).
       - Forge выполняет рендер изображения с активным ADetailer (лица и кисти рук).
       - Переключение VRAMArbiter обратно на LLM (выгрузка Forge, восстановление LLM в VRAM).
    """
    scen = "Сценарий 3: RTX 5050 Laptop (Blackwell) - Полный цикл"

    # 1. Мокирование детекции оборудования Blackwell
    hw_profile_blackwell = {
        "supported": True,
        "gpu_name": "NVIDIA GeForce RTX 5050 Laptop GPU",
        "vendor": "nvidia",
        "vram_total_mb": 8192,
        "compute_cap": "12.0",
        "recommended_kv": "q8_0",
        "recommended_fa": True,
        "cpu_avx2": True,
    }
    monkeypatch.setattr(LayerBudgeter, "has_cpu_avx2", lambda: True)
    monkeypatch.setattr(LayerBudgeter, "detect_hardware_kv_profile", classmethod(lambda cls: hw_profile_blackwell))
    monkeypatch.setattr("services.art_director.vram_policy.resolve_hardware_vram_budget", lambda monitor=None: (8.0, 700))

    profile = LayerBudgeter.detect_hardware_kv_profile()
    assert_diag(profile["compute_cap"] == "12.0", scen, "Compute Capability Blackwell", "12.0", profile["compute_cap"])
    assert_diag(profile["recommended_kv"] == "q8_0", scen, "Рекомендация квантования KV", "q8_0", profile["recommended_kv"])
    assert_diag(profile["recommended_fa"] is True, scen, "Поддержка Flash Attention", True, profile["recommended_fa"])

    # 2. Стартовые флаги Forge для Blackwell 8 ГБ:
    # 8 ГБ (>= 6 ГБ) -> стартовый --always-offload-from-vram НЕ добавляется
    # CC 12.0 (>= 8.0) -> --vae-in-bf16, без --no-half-vae и без --vae-in-fp32
    flags, meta = vram_policy.resolve_forge_startup_flags(
        total_ram_gb=15.7,
        avail_ram_mb=8000.0,
    )
    assert_diag("--vae-in-bf16" in flags, scen, "Флаг --vae-in-bf16 на Blackwell", True, "--vae-in-bf16" in flags)
    assert_diag("--no-half-vae" not in flags, scen, "Отсутствие --no-half-vae на Blackwell", True, "--no-half-vae" not in flags)
    assert_diag("--vae-in-fp32" not in flags, scen, "Отсутствие --vae-in-fp32 на Blackwell", True, "--vae-in-fp32" not in flags)
    assert_diag(
        "--always-offload-from-vram" not in flags,
        scen,
        "Отсутствие стартового оффлоада на 8 ГБ VRAM",
        True,
        "--always-offload-from-vram" not in flags
    )
    assert_diag_eq(meta["vae_precision"], "bf16", scen, "Точность VAE в метаданных")

    # 3. Расчет оффлоада LLM и формирование параметров запуска
    fake_model = tmp_path / "qwen2.5-coder-7b.gguf"
    fake_model.write_bytes(b"GGUF" + b"\x00" * 4096)
    mock_meta_7b = {
        "model_size_mb": 4500.0,
        "total_layers": 32,
        "vocab_size": 152064,
    }
    with patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta_7b):
        ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
            model_path=str(fake_model),
            target_context=8192,
            total_vram_mb=8192,
            available_ram_mb=16384,
            cache_type="q8_0",
        )
        assert_diag(can_run is True, scen, "Возможность запуска 7B модели на 8 ГБ VRAM", True, can_run)
        assert_diag(ngl > 0, scen, "Оффлоад слоев на GPU для RTX 5050", "ngl > 0", ngl)

    # Проверка разрешения аргументов mmap для современного llama-server
    fake_server_exe = tmp_path / "bin" / "llama-server.exe"
    fake_server_exe.parent.mkdir(parents=True, exist_ok=True)
    fake_server_exe.write_bytes(b"MZ" + b"\x00" * 2048)

    mock_help_proc = MagicMock()
    mock_help_proc.stdout = "--load-mode none    disable mmap and load model weights directly into memory"
    mock_help_proc.stderr = ""
    with patch("subprocess.run", return_value=mock_help_proc):
        mmap_args = ServerConfigResolver.resolve_mmap_disable_args(server_exe=fake_server_exe)
        assert_diag(
            "--load-mode" in mmap_args or "--no-mmap" in mmap_args,
            scen,
            "Параметр отключения mmap в командной строке",
            "Наличие --load-mode или --no-mmap",
            mmap_args
        )

    # 4. Сквозной цикл арбитража VRAM и инференса (LLM -> VRAMArbiter -> ArtDirector -> ForgeClient -> VRAMArbiter -> LLM)
    outputs_dir = tmp_path / "outputs_rtx5050"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = "Готов к работе. Давайте напишем код."
    mock_lm.unload_models_cli.return_value = True

    real_forge_client = ForgeClient(base_url="http://127.0.0.1:7860")
    monkeypatch.setattr(real_forge_client, "is_server_ready", lambda timeout=1.0: True)
    monkeypatch.setattr(real_forge_client, "_wait_for_vram_clear", lambda: None)

    captured_payloads = []
    valid_b64 = _make_png_b64()

    def mock_scenario3_post(url, json=None, timeout=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "/sdapi/v1/txt2img" in str(url):
            captured_payloads.append(json)
            resp.json.return_value = {"images": [valid_b64], "info": "{}"}
        else:
            resp.json.return_value = {}
        return resp

    def mock_scenario3_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "/sdapi/v1/sd-models" in str(url):
            resp.json.return_value = [{"title": "realistic", "model_name": "realistic"}]
        elif "/sdapi/v1/options" in str(url):
            resp.json.return_value = {"sd_model_checkpoint": "realistic"}
        elif "/sdapi/v1/progress" in str(url):
            resp.json.return_value = {"progress": 1.0, "state": {"step": 20, "job_count": 1}}
        else:
            resp.json.return_value = {}
        return resp

    monkeypatch.setattr("services.forge_client.requests.post", mock_scenario3_post)
    monkeypatch.setattr("services.forge_client.requests.get", mock_scenario3_get)
    monkeypatch.setattr(
        "services.forge_client.requests.Session.post",
        lambda self, url, json=None, **kwargs: mock_scenario3_post(url, json=json, **kwargs),
    )

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=real_forge_client)

    # Фаза А: Текстовый диалог (LLM)
    arbiter.prepare_for_task("llm")
    assert_diag(arbiter._current_owner == GPUOwner.LLM, scen, "Владелец GPU: LLM", GPUOwner.LLM, arbiter._current_owner)
    text_reply = mock_lm.chat_completion(messages=[{"role": "user", "content": "Привет"}])
    assert_diag(mock_lm.chat_completion.called, scen, "Вызов генерации текста LLM", True, mock_lm.chat_completion.called)
    assert_diag("Готов к работе" in text_reply, scen, "Результат текстового инференса", "Готов к работе", text_reply)

    # Фаза Б: Запрос на генерацию арта -> переключение VRAM на Forge
    arbiter.prepare_for_task("image")
    assert_diag(arbiter._current_owner == GPUOwner.DIFFUSION, scen, "Владелец GPU: Diffusion", GPUOwner.DIFFUSION, arbiter._current_owner)
    assert_diag(mock_lm.unload_models_cli.called, scen, "Выгрузка LLM из VRAM перед стартом Forge", True, mock_lm.unload_models_cli.called)

    director = ArtDirector()
    intent = VisualIntent(
        prompt="cyberpunk girl, neon lights, 50mm portrait",
        aspect_ratio="1:1",
        mode="assistant",
    )
    plan = director.plan_from_visual_intent(visual_intent=intent, mode="assistant")
    forge_kwargs = plan.to_forge_kwargs(output_dir=outputs_dir)

    img_res = real_forge_client.text_to_image(**forge_kwargs)
    assert_diag(img_res.is_file(), scen, "Создание файла изображения реальным ForgeClient", True, img_res.is_file())
    assert_diag(len(captured_payloads) == 1, scen, "Запрос к Forge /sdapi/v1/txt2img отправлен", 1, len(captured_payloads))

    txt2img_payload = captured_payloads[0]
    assert_diag("alwayson_scripts" in txt2img_payload, scen, "Наличие alwayson_scripts в payload Forge", True, "alwayson_scripts" in txt2img_payload)
    assert_diag(
        "ADetailer" in txt2img_payload.get("alwayson_scripts", {}),
        scen,
        "Наличие блока ADetailer в alwayson_scripts",
        True,
        "ADetailer" in txt2img_payload.get("alwayson_scripts", {})
    )

    ad_block = txt2img_payload["alwayson_scripts"]["ADetailer"]
    ad_args = ad_block.get("args", [])
    assert_diag(len(ad_args) >= 3, scen, "Аргументы ADetailer содержат лицо и настройки", True, len(ad_args) >= 3)
    assert_diag(ad_args[0] is True, scen, "Мастер-флаг активации ADetailer", True, ad_args[0])

    # Фаза В: Возврат к общению с текстовым ассистентом -> выгрузка Forge и возврат LLM
    arbiter.prepare_for_task("llm")
    assert_diag(arbiter._current_owner == GPUOwner.LLM, scen, "Возврат владельца GPU под LLM", GPUOwner.LLM, arbiter._current_owner)
    assert_diag(
        real_forge_client._checkpoint_unloaded is True,
        scen,
        "Выгрузка чекпоинта Forge из VRAM в RAM",
        True,
        real_forge_client._checkpoint_unloaded
    )
