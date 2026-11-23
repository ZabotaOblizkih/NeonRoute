"""services/art_director/vram_policy.py

Чистый математический модуль для динамического расчета бюджета VRAM,
адаптивной политики памяти SD WebUI Forge (FAST_KEEP_VRAM / SMART_SWAP / STAGE_OFFLOAD),
безопасного порога Hires.fix и защиты системной RAM от переполнения --pin-shared-memory.
Изолирован от Qt, ForgeClient, SystemMonitor и Director (Pure Logic).
"""

import logging
import math
import sys
import time
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List
from unittest.mock import NonCallableMock

logger = logging.getLogger(__name__)

_HARDWARE_BUDGET_CACHE_TTL_SEC = 5.0
_hardware_budget_cache: Optional[Tuple[float, float, int]] = None  # (monotonic_ts, total_vram_gb, os_reserve_mb)

# Базовые константы весов (для Hires.fix gate сохраняем консервативные ориентиры)
WEIGHTS_SD15_MB = 2100
WEIGHTS_SDXL_FP8_MB = 3950
WEIGHTS_SDXL_FP16_MB = 6758

# Калиброванные активные веса в фазе сэмплинга Forge (CLIP выгружен после кодирования)
ACTIVE_WEIGHTS_SD15_MB = 2100
ACTIVE_WEIGHTS_SDXL_FP8_MB = 3950
ACTIVE_WEIGHTS_SDXL_FP16_MB = 6350

DEFAULT_FORGE_RESERVE_MB = 450
DEFAULT_SAFETY_MARGIN_MB = 150
DEFAULT_OS_RESERVE_MB = 700
ADETAILER_RESERVE_MB = 650

# Базовые константы стоимости холста и этапа Hires (MB)
BASE_CANVAS_FIXED_SD15_MB = 420
BASE_CANVAS_PER_MPIX_SD15_MB = 350
BASE_CANVAS_FIXED_SDXL_MB = 520
BASE_CANVAS_PER_MPIX_SDXL_MB = 380

HIRES_PER_MPIX_SD15_MB = 520
HIRES_PER_MPIX_SDXL_MB = 700
VAE_TILING_FACTOR = 0.65

# Устаревшие эвристические константы (сохранены как алиасы для обратной совместимости импортов)
K_ARCH_SD15 = 170_000
K_ARCH_SDXL = 95_000

LORA_ADAPTER_WEIGHT_MB = 120
VAE_FP32_OVERHEAD_MB = 260


def _clamp_int(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


def resolve_checkpoint_file_size_mb(
    checkpoint_name: Optional[str],
    search_dirs: Optional[List[Path]] = None,
) -> Optional[float]:
    """Возвращает размер файла чекпоинта в МБ (0 мс stat на диске) или None."""
    if not checkpoint_name:
        return None
    try:
        raw_path = Path(str(checkpoint_name).strip())
        if raw_path.is_file():
            return raw_path.stat().st_size / (1024.0 * 1024.0)

        candidates: List[Path] = []
        if search_dirs:
            candidates.extend(search_dirs)
        else:
            try:
                from config import FORGE_ROOT_DIR, get_forge_models_dir
                candidates.append(get_forge_models_dir())
                candidates.extend([
                    FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion",
                    FORGE_ROOT_DIR / "webui" / "models" / "Stable-diffusion",
                    FORGE_ROOT_DIR / "models" / "Stable-diffusion",
                ])
            except Exception:
                pass

        fname = raw_path.name
        for base_dir in candidates:
            if not base_dir:
                continue
            try:
                cand = Path(base_dir) / fname
                if cand.is_file():
                    return cand.stat().st_size / (1024.0 * 1024.0)
            except Exception:
                continue
    except Exception:
        return None
    return None


def estimate_active_model_weights_mb(
    architecture: str = "sd15",
    is_fp8: bool = True,
    checkpoint_size_mb: Optional[float] = None,
) -> int:
    """Оценивает рабочий вес модели в фазе сэмплинга Forge (CLIP уже выгружен после encode)."""
    arch = str(architecture or "sd15").strip().lower()
    is_xl = "xl" in arch or any(m in arch for m in ("pony", "dmd", "insta", "mop"))

    if is_xl:
        if is_fp8:
            if checkpoint_size_mb is not None and checkpoint_size_mb > 0:
                if checkpoint_size_mb <= 4600:
                    # Уже квантованный FP8-чекпоинт на диске
                    return _clamp_int(checkpoint_size_mb + 250, 3800, 4300)
                # FP16-чекпоинт, сжимаемый Forge в fp8e4 на лету
                return _clamp_int(checkpoint_size_mb * 0.60, 3800, 4300)
            return ACTIVE_WEIGHTS_SDXL_FP8_MB
        if checkpoint_size_mb is not None and checkpoint_size_mb > 0:
            return _clamp_int(checkpoint_size_mb * 0.96, 6200, 6500)
        return ACTIVE_WEIGHTS_SDXL_FP16_MB

    if checkpoint_size_mb is not None and checkpoint_size_mb > 0:
        if checkpoint_size_mb <= 3200:
            return _clamp_int(checkpoint_size_mb, 1950, 2450)
        # Unpruned чекпоинт с EMA: в инференс идёт только рабочий UNet/VAE
        return _clamp_int(checkpoint_size_mb * 0.55, 2100, 2600)
    return ACTIVE_WEIGHTS_SD15_MB


def calculate_max_hires_canvas_area(
    architecture: str = "sd15",
    is_fp8: bool = False,
    total_vram_gb: float = 8.0,
    os_reserve_mb: int = DEFAULT_OS_RESERVE_MB,
    enable_adetailer: bool = False,
    active_lora_count: int = 0,
    checkpoint_size_mb: Optional[float] = None,
    enable_hand_adetailer: bool = False,
    vae_tiling: bool = True,
    width: int = 0,
    height: int = 0,
    hr_upscaler: str = "Latent",
    ad_inpaint_size: int = 512,
) -> int:
    """Вычисляет максимальную допустимую площадь кадра (в пикселях) для Hires.fix
    через обратную формулу пикового потребления VRAM, согласованную с resolve_forge_offload_policy.
    """
    arch = str(architecture).strip().lower()
    is_xl = "xl" in arch or any(m in arch for m in ("pony", "dmd", "insta", "mop"))

    total_vram_mb = int(round(float(total_vram_gb) * 1024.0))
    usable_vram_mb = max(0, total_vram_mb - int(os_reserve_mb) - DEFAULT_FORGE_RESERVE_MB)

    if is_xl and not is_fp8 and (checkpoint_size_mb is None or checkpoint_size_mb <= 0):
        base_weights_mb = WEIGHTS_SDXL_FP16_MB
    else:
        base_weights_mb = estimate_active_model_weights_mb(
            architecture=arch,
            is_fp8=is_fp8,
            checkpoint_size_mb=checkpoint_size_mb,
        )
    lora_mb = max(0, int(active_lora_count)) * LORA_ADAPTER_WEIGHT_MB
    total_weights_mb = base_weights_mb + lora_mb + VAE_FP32_OVERHEAD_MB

    clean_w = int(width) if (width and width > 0) else (1024 if is_xl else 512)
    clean_h = int(height) if (height and height > 0) else (1024 if is_xl else 768)
    base_mpix = (clean_w * clean_h) / 1_000_000.0

    if is_xl:
        base_canvas_mb = int(BASE_CANVAS_FIXED_SDXL_MB + base_mpix * BASE_CANVAS_PER_MPIX_SDXL_MB)
    else:
        base_canvas_mb = int(BASE_CANVAS_FIXED_SD15_MB + base_mpix * BASE_CANVAS_PER_MPIX_SD15_MB)

    upscaler_mb = 0 if "latent" in str(hr_upscaler or "Latent").lower() else 220

    if enable_adetailer:
        inpaint_sz = max(512, int(ad_inpaint_size or 512))
        yolo_mb = 80 + (80 if enable_hand_adetailer else 0)
        if is_xl:
            inpaint_buf_mb = 1550 if inpaint_sz >= 768 else 1250
        else:
            inpaint_buf_mb = 650 if inpaint_sz >= 768 else 450
        hands_extra_mb = 150 if enable_hand_adetailer else 0
        ad_stage_mb = yolo_mb + inpaint_buf_mb + hands_extra_mb
    else:
        ad_stage_mb = 0

    avail_mb = usable_vram_mb - DEFAULT_SAFETY_MARGIN_MB - total_weights_mb
    avail_gb = avail_mb / 1024.0

    # Защита от OOM / медленного STAGE_OFFLOAD для SDXL FP16:
    # На картах <= 8.5 ГБ VRAM SDXL FP16 (~6.5 ГБ) не имеет достаточного запаса под Hires;
    # на картах > 8.5 ГБ требуется минимум 4.5 ГБ динамической памяти
    if is_xl and not is_fp8:
        if total_vram_gb <= 8.5 or avail_gb < 4.5:
            return 0

    if is_xl and is_fp8 and vae_tiling:
        # Для SDXL FP8 с VAE Tiling допускаем контролируемый порог SMART_SWAP:
        # +300 МБ к базовому safe budget + компенсация оверхеда VAE для контролируемого свопа слоев в RAM
        target_budget_mb = usable_vram_mb - DEFAULT_SAFETY_MARGIN_MB + 300 + VAE_FP32_OVERHEAD_MB
    else:
        target_budget_mb = usable_vram_mb - DEFAULT_SAFETY_MARGIN_MB

    free_for_hr_mb = target_budget_mb - (total_weights_mb + base_canvas_mb + upscaler_mb)
    if free_for_hr_mb <= 0:
        return 0

    cost_per_mpix = (HIRES_PER_MPIX_SDXL_MB if is_xl else HIRES_PER_MPIX_SD15_MB) * (VAE_TILING_FACTOR if vae_tiling else 1.0)
    area = int((free_for_hr_mb / cost_per_mpix) * 1_000_000)

    if is_xl:
        if total_vram_gb <= 8.5:
            area = min(area, 2_400_000)
    else:
        area = min(area, 2_400_000)

    return max(0, area)


def is_hires_safe(
    width: int,
    height: int,
    architecture: str = "sd15",
    is_fp8: bool = False,
    total_vram_gb: float = 8.0,
    os_reserve_mb: int = DEFAULT_OS_RESERVE_MB,
    enable_adetailer: bool = False,
    active_lora_count: int = 0,
    hr_scale: float = 1.0,
    checkpoint_size_mb: Optional[float] = None,
    enable_hand_adetailer: bool = False,
    vae_tiling: bool = True,
    hr_upscaler: str = "Latent",
    ad_inpaint_size: int = 512,
) -> bool:
    """Определяет, безопасна ли генерация заданного разрешения с Hires.fix."""
    if width <= 0 or height <= 0:
        return False

    max_allowed_area = calculate_max_hires_canvas_area(
        architecture=architecture,
        is_fp8=is_fp8,
        total_vram_gb=total_vram_gb,
        os_reserve_mb=os_reserve_mb,
        enable_adetailer=enable_adetailer,
        active_lora_count=active_lora_count,
        checkpoint_size_mb=checkpoint_size_mb,
        enable_hand_adetailer=enable_hand_adetailer,
        vae_tiling=vae_tiling,
        width=width,
        height=height,
        hr_upscaler=hr_upscaler,
        ad_inpaint_size=ad_inpaint_size,
    )

    if max_allowed_area <= 0:
        return False

    scale = max(1.0, float(hr_scale or 1.0))
    target_w = width * scale
    target_h = height * scale
    base_area = target_w * target_h
    ratio = max(width / height, height / width)

    # Защита от экстремальных соотношений (Aspect Ratio Penalty)
    if ratio > 1.8:
        penalty_factor = 1.0 + (ratio - 1.8) * 0.1
        effective_area = base_area * penalty_factor
    else:
        effective_area = float(base_area)

    return effective_area <= max_allowed_area


def resolve_safe_hires_scale(
    width: int,
    height: int,
    requested_hr_scale: float = 1.5,
    architecture: str = "sd15",
    is_fp8: bool = True,
    enable_adetailer: bool = False,
    enable_hand_adetailer: bool = False,
    active_lora_count: int = 0,
    hr_upscaler: str = "Latent",
    vae_tiling: bool = True,
    checkpoint_size_mb: Optional[float] = None,
    system_monitor: Optional[Any] = None,
    ad_inpaint_size: int = 512,
    free_vram_mb: Optional[int] = None,
    forge_holds_vram: bool = False,
) -> Dict[str, Any]:
    """Адаптивно подбирает безопасный масштаб Hires.fix под реальный бюджет VRAM без перехода в STAGE_OFFLOAD."""
    req_scale = float(requested_hr_scale or 1.0)
    if req_scale <= 1.0:
        return {
            "allowed": False,
            "effective_scale": 1.0,
            "requested_scale": req_scale,
            "hr_width": width,
            "hr_height": height,
            "max_area": 0,
            "policy": None,
            "reason": "отключено в профиле (scale <= 1.0)",
        }

    total_vram_gb, os_reserve_mb = resolve_hardware_vram_budget(system_monitor)
    max_area = calculate_max_hires_canvas_area(
        architecture=architecture,
        is_fp8=is_fp8,
        total_vram_gb=total_vram_gb,
        os_reserve_mb=os_reserve_mb,
        enable_adetailer=enable_adetailer,
        active_lora_count=active_lora_count,
        checkpoint_size_mb=checkpoint_size_mb,
        enable_hand_adetailer=enable_hand_adetailer,
        vae_tiling=vae_tiling,
        width=width,
        height=height,
        hr_upscaler=hr_upscaler,
        ad_inpaint_size=ad_inpaint_size,
    )

    if max_area <= 0:
        return {
            "allowed": False,
            "effective_scale": 1.0,
            "requested_scale": req_scale,
            "hr_width": width,
            "hr_height": height,
            "max_area": 0,
            "policy": None,
            "reason": "недостаточно VRAM (SDXL FP16 на <=8GB или дефицит памяти)",
        }

    ratio = max(width / height, height / width) if (height > 0 and width > 0) else 1.0
    penalty_factor = (1.0 + (ratio - 1.8) * 0.1) if ratio > 1.8 else 1.0

    # Формируем сетку кандидатов от requested_hr_scale вниз до 1.15 с шагом 0.05
    candidates = []
    curr = round(req_scale, 2)
    while curr >= 1.149:
        candidates.append(round(curr, 2))
        curr = round(curr - 0.05, 2)
    if req_scale > 1.15 and 1.15 not in candidates:
        candidates.append(1.15)

    base_area = width * height
    for s in candidates:
        cand_w = max(64, int(round((width * s) / 64.0)) * 64)
        cand_h = max(64, int(round((height * s) / 64.0)) * 64)
        cand_area = cand_w * cand_h

        # 1. Должен быть реальный прирост разрешения
        if cand_area <= base_area:
            continue

        # 2. Эффективная площадь с учетом штрафа за соотношение сторон не должна превышать потолок
        if (cand_area * penalty_factor) > max_area:
            continue

        # 3. Проверка через resolve_forge_offload_policy: не уходит ли в STAGE_OFFLOAD
        pol = resolve_forge_offload_policy(
            width=width,
            height=height,
            architecture=architecture,
            is_fp8=is_fp8,
            enable_adetailer=enable_adetailer,
            enable_hand_adetailer=enable_hand_adetailer,
            active_lora_count=active_lora_count,
            system_monitor=system_monitor,
            free_vram_mb=free_vram_mb,
            enable_hr=True,
            hr_scale=s,
            hr_upscaler=hr_upscaler,
            ad_inpaint_size=ad_inpaint_size,
            vae_tiling=vae_tiling,
            checkpoint_size_mb=checkpoint_size_mb,
            forge_holds_vram=forge_holds_vram,
        )

        can_run = bool(pol.get("can_run", False))
        force_dis = bool(pol.get("force_disable_hr", False))
        mode = pol.get("mode", "")

        if can_run and not force_dis and mode in ("FAST_KEEP_VRAM", "SMART_SWAP"):
            eff_s = round(s, 2)
            if abs(eff_s - req_scale) < 1e-4:
                reason = "в пределах бюджета VRAM"
            else:
                reason = f"подогнано под бюджет VRAM ({req_scale:.2f}x -> {eff_s:.2f}x, {mode})"
            return {
                "allowed": True,
                "requested_scale": req_scale,
                "effective_scale": eff_s,
                "hr_width": cand_w,
                "hr_height": cand_h,
                "max_area": max_area,
                "policy": pol,
                "reason": reason,
            }

    return {
        "allowed": False,
        "effective_scale": 1.0,
        "requested_scale": req_scale,
        "hr_width": width,
        "hr_height": height,
        "max_area": max_area,
        "policy": None,
        "reason": "превышен потолок площади / переход в STAGE_OFFLOAD",
    }


def resolve_hires_second_pass_steps(
    base_steps: int,
    denoising_strength: float = 0.35,
    configured_steps: Optional[int] = None,
) -> int:
    """Динамический расчёт числа шагов второго прохода Hires.fix.

    Учитывает число базовых шагов генерации и силу денойза (denoising_strength),
    сохраняя приоритет явного значения из конфигурации профиля модели.
    """
    if configured_steps is not None:
        try:
            val = int(configured_steps)
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass

    clean_base = max(1, int(base_steps or 25))
    if clean_base <= 10:
        return max(4, min(10, clean_base))

    clean_denoise = max(0.15, float(denoising_strength or 0.35))
    base_hr = min(20, max(12, int(round(clean_base * 0.60))))
    min_for_denoise = math.ceil(3.0 / clean_denoise)
    return min(22, max(base_hr, min_for_denoise))


def resolve_hardware_vram_budget(system_monitor=None, use_cache: bool = True) -> Tuple[float, int]:
    """Безопасный адаптер к метрикам оборудования (SSOT).

    Возвращает (total_vram_gb, os_reserve_mb).
    Кэширует результат на 5.0 с для исключения многократных вызовов LayerBudgeter
    при подготовке одного кадра (автоматически обходит кэш при моках в тестах).
    """
    global _hardware_budget_cache

    total_vram_gb = 8.0
    os_reserve_mb = DEFAULT_OS_RESERVE_MB

    is_mocked = isinstance(system_monitor, NonCallableMock) or (
        "pynvml" in sys.modules and isinstance(sys.modules["pynvml"], NonCallableMock)
    )
    try:
        from services.layer_budgeter import LayerBudgeter
        if isinstance(getattr(LayerBudgeter, "detect_gpu_topology", None), NonCallableMock):
            is_mocked = True
    except Exception:
        LayerBudgeter = None  # type: ignore

    now = time.monotonic()
    if (
        use_cache
        and not is_mocked
        and _hardware_budget_cache is not None
        and (now - _hardware_budget_cache[0]) < _HARDWARE_BUDGET_CACHE_TTL_SEC
    ):
        return _hardware_budget_cache[1], _hardware_budget_cache[2]

    # 1. Считывание топологии дисплея из LayerBudgeter
    try:
        if LayerBudgeter is not None:
            _, detected_reserve = LayerBudgeter.detect_gpu_topology()
            os_reserve_mb = int(detected_reserve)
    except Exception as e:
        logger.debug(f"[VRAMPolicy] Fallback топологии дисплея: {e}")

    # 2. Опрос физического объема VRAM через SystemMonitor / NVML
    try:
        if system_monitor and hasattr(system_monitor, "_last_emitted_stats") and system_monitor._last_emitted_stats:
            mb = system_monitor._last_emitted_stats.get("vram_total_mb", 8192)
            total_vram_gb = round(mb / 1024.0, 1)
        elif system_monitor and hasattr(system_monitor, "nvml_initialized") and system_monitor.nvml_initialized:
            import pynvml
            info = pynvml.nvmlDeviceGetMemoryInfo(system_monitor.gpu_handle)
            total_vram_gb = round(info.total / (1024.0 ** 3), 1)
        elif system_monitor is None:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            total_vram_gb = round(info.total / (1024.0 ** 3), 1)
    except Exception as e:
        logger.debug(f"[VRAMPolicy] Fallback объема VRAM: {e}")

    if use_cache and not is_mocked:
        _hardware_budget_cache = (now, total_vram_gb, os_reserve_mb)

    return total_vram_gb, os_reserve_mb


def _parse_compute_cap(cap: Any) -> Tuple[int, int]:
    """Парсит compute capability из кортежа (8, 9), float 8.9 или строки '8.9' / '12.0'."""
    if isinstance(cap, (tuple, list)) and len(cap) >= 2:
        try:
            return int(cap[0]), int(cap[1])
        except (ValueError, TypeError):
            return (0, 0)
    if isinstance(cap, (int, float)):
        major = int(cap)
        minor = int(round((cap - major) * 10))
        return major, minor
    if isinstance(cap, str):
        parts = cap.strip().split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
        elif len(parts) == 1 and parts[0].isdigit():
            return int(parts[0]), 0
    return (0, 0)


def should_enable_pin_shared_memory(
    llm_active: bool = False,
    sdxl_fp8_enabled: Optional[bool] = None,
    total_ram_gb: Optional[float] = None,
    avail_ram_mb: Optional[float] = None,
) -> Tuple[bool, str]:
    """Определяет безопасность флага --pin-shared-memory по объему системной RAM.

    Page-Locked (pinned) память не выгружается в файл подкачки Windows. На системах
    с <= 16 ГБ RAM при активной/выгруженной в RAM LLM и тяжелых 6.5 ГБ чекпоинтах
    включение --pin-shared-memory может спровоцировать Host RAM OOM.
    """
    if total_ram_gb is None or avail_ram_mb is None:
        try:
            import psutil
            vm = psutil.virtual_memory()
            if total_ram_gb is None:
                total_ram_gb = vm.total / (1024.0 ** 3)
            if avail_ram_mb is None:
                avail_ram_mb = vm.available / (1024.0 ** 2)
        except Exception:
            return True, "psutil unavailable -> default pin-shared-memory enabled"

    if sdxl_fp8_enabled is None:
        try:
            from utils.config_manager import ConfigManager
            sdxl_fp8_enabled = bool(ConfigManager.load_config().get("forge_sdxl_fp8_enabled", True))
        except Exception:
            sdxl_fp8_enabled = True

    total_gb = float(total_ram_gb)
    avail_mb = float(avail_ram_mb)

    if total_gb > 16.5:
        if avail_mb < 4096:
            return False, f"RAM {total_gb:.1f}GB, свободно только {int(avail_mb)}MB (<4096MB)"
        return True, f"RAM {total_gb:.1f}GB (>16GB), свободно {int(avail_mb)}MB"

    # Профиль <= 16.5 ГБ системной ОЗУ
    if llm_active:
        return (
            False,
            f"RAM {total_gb:.1f}GB <= 16GB с активной LLM: pin-shared-memory отключен во избежание Host RAM OOM",
        )

    if not sdxl_fp8_enabled and avail_mb < 9000:
        return (
            False,
            f"RAM {total_gb:.1f}GB <= 16GB, SDXL FP8 выключен (риск 6.5ГБ STAGE_OFFLOAD pin) и свободно {int(avail_mb)}MB",
        )

    min_avail_mb = 9000
    if avail_mb < min_avail_mb:
        return (
            False,
            f"RAM {total_gb:.1f}GB <= 16GB, свободно {int(avail_mb)}MB < {min_avail_mb}MB",
        )

    return True, f"RAM {total_gb:.1f}GB <= 16GB, достаточно свободной ОЗУ ({int(avail_mb)}MB)"


def resolve_forge_startup_flags(
    llm_active: bool = False,
    extra_args: Optional[List[str]] = None,
    system_monitor: Optional[Any] = None,
    total_ram_gb: Optional[float] = None,
    avail_ram_mb: Optional[float] = None,
    ad_no_huggingface: Optional[bool] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Формирует стартовые CLI-флаги Forge без принудительного --always-offload-from-vram на 8 ГБ.

    Управление режимами FAST_KEEP_VRAM / SMART_SWAP / STAGE_OFFLOAD происходит динамически
    перед каждым запросом через /sdapi/v1/free-vram.
    """
    total_vram_gb, _ = resolve_hardware_vram_budget(system_monitor)
    flags: List[str] = [
        "--api",
        "--port", "7860",
        "--listen",
        "--cuda-malloc",
        "--opt-sdp-attention",
        "--no-download-sd-model",
        "--skip-torch-cuda-test",
        "--no-hashing",
        "--disable-nan-check",
    ]

    if ad_no_huggingface is None:
        try:
            from utils.config_manager import ConfigManager
            ad_no_huggingface = bool(ConfigManager.load_config().get("forge_ad_no_huggingface", False))
        except Exception:
            ad_no_huggingface = False

    if ad_no_huggingface:
        flags.append("--ad-no-huggingface")

    vae_precision = "fp32"
    try:
        from services.layer_budgeter import LayerBudgeter
        hw_profile = LayerBudgeter.detect_hardware_kv_profile()
        vendor = str(hw_profile.get("vendor", "")).lower()
        cap = _parse_compute_cap(hw_profile.get("compute_cap"))
        if vendor == "nvidia" and cap >= (8, 0):
            flags.append("--vae-in-bf16")
            vae_precision = "bf16"
        else:
            flags.extend(["--no-half-vae", "--vae-in-fp32"])
    except Exception as e:
        logger.debug(f"[VRAMPolicy] Ошибка детекции профиля VAE: {e}")
        flags.extend(["--no-half-vae", "--vae-in-fp32"])

    pin_ok, pin_reason = should_enable_pin_shared_memory(
        llm_active=llm_active,
        total_ram_gb=total_ram_gb,
        avail_ram_mb=avail_ram_mb,
    )
    if pin_ok:
        flags.append("--pin-shared-memory")

    # Только для ультра-малых GPU (< 6 ГБ) ставим стартовый оффлоад по умолчанию
    force_startup_offload = total_vram_gb < 6.0
    if force_startup_offload:
        flags.append("--always-offload-from-vram")

    if extra_args:
        seen = set(flags)
        for arg in extra_args:
            if arg and arg not in seen:
                seen.add(arg)
                flags.append(arg)

    meta = {
        "total_vram_gb": total_vram_gb,
        "pin_shared_memory": pin_ok,
        "pin_reason": pin_reason,
        "startup_always_offload": force_startup_offload,
        "vae_precision": vae_precision,
        "ad_no_huggingface": bool(ad_no_huggingface),
    }
    return flags, meta


def resolve_forge_offload_policy(
    width: int = 896,
    height: int = 1152,
    architecture: str = "sd15",
    is_fp8: bool = True,
    enable_adetailer: bool = True,
    active_lora_count: int = 0,
    system_monitor: Optional[Any] = None,
    free_vram_mb: Optional[int] = None,
    enable_hr: bool = False,
    hr_scale: float = 1.0,
    hr_upscaler: str = "Latent",
    enable_hand_adetailer: bool = False,
    ad_inpaint_size: int = 512,
    vae_tiling: bool = False,
    checkpoint_size_mb: Optional[float] = None,
    forge_holds_vram: bool = False,
    total_ram_mb: Optional[float] = None,
    available_ram_mb: Optional[float] = None,
) -> Dict[str, Any]:
    """Вычисляет оптимальный режим памяти Forge по реальному пику этапа, бюджету VRAM и системной RAM.

    Режимы:
    - FAST_KEEP_VRAM (always_offload=False): все этапы помещаются в VRAM без выгрузок.
    - SMART_SWAP (always_offload=False, увеличенный inference_memory_mb): пограничный бюджет
      для SDXL FP8 с Hires+AD или чистой SDXL FP16 — частичный своп слоёв в RAM.
    - STAGE_OFFLOAD (always_offload=True): тяжелый пайплайн (SDXL FP16 + Hires/AD, либо
      ограниченная свободная VRAM) — поэтапная выгрузка моделей для исключения OOM и
      торможения по PCIe на каждом шаге сэмплинга.
    """
    total_vram_gb, os_reserve_mb = resolve_hardware_vram_budget(system_monitor)
    total_vram_mb = int(round(float(total_vram_gb) * 1024.0))
    usable_vram_mb = max(0, total_vram_mb - int(os_reserve_mb) - DEFAULT_FORGE_RESERVE_MB)

    # Опрос системной RAM для Host RAM Starvation Guard
    if total_ram_mb is None or available_ram_mb is None:
        try:
            import psutil
            vm = psutil.virtual_memory()
            vm_total = getattr(vm, "total", None)
            vm_avail = getattr(vm, "available", None)
            if total_ram_mb is None and isinstance(vm_total, (int, float)) and vm_total > 0:
                total_ram_mb = vm_total / (1024.0 * 1024.0)
            if available_ram_mb is None and isinstance(vm_avail, (int, float)) and vm_avail >= 0:
                available_ram_mb = vm_avail / (1024.0 * 1024.0)
        except Exception:
            pass
    eff_total_ram_mb = float(total_ram_mb) if isinstance(total_ram_mb, (int, float)) else 16384.0
    eff_avail_ram_mb = float(available_ram_mb) if isinstance(available_ram_mb, (int, float)) else 8192.0

    if free_vram_mb is None and not forge_holds_vram and system_monitor is not None:
        has_real_telemetry = bool(
            getattr(system_monitor, "nvml_initialized", False)
            or getattr(system_monitor, "_last_emitted_stats", None)
        )
        if has_real_telemetry:
            try:
                free_vram_mb = system_monitor.get_free_vram_mb()
            except Exception:
                free_vram_mb = None

    arch = str(architecture or "sd15").strip().lower()
    is_xl = "xl" in arch or any(m in arch for m in ("pony", "dmd", "insta", "mop"))

    # Host RAM Starvation Guard: критический дефицит ОЗУ при малом VRAM на тяжёлом SDXL
    host_ram_starved = bool(eff_total_ram_mb <= 6144 or eff_avail_ram_mb <= 2500)
    ram_vram_emergency = bool(host_ram_starved and total_vram_mb <= 6144 and is_xl)
    force_fp8_storage = bool(ram_vram_emergency and not is_fp8)
    disable_secondary_adetailer = bool(ram_vram_emergency)
    force_disable_hr = bool(ram_vram_emergency)

    eff_is_fp8 = bool(is_fp8 or force_fp8_storage)
    eff_hand_ad = bool(enable_hand_adetailer and not disable_secondary_adetailer)
    eff_scale = max(1.0, float(hr_scale or 1.0)) if (enable_hr and not force_disable_hr) else 1.0
    hr_active = bool(enable_hr and not force_disable_hr and eff_scale > 1.0)

    base_weights_mb = estimate_active_model_weights_mb(
        architecture=arch,
        is_fp8=eff_is_fp8,
        checkpoint_size_mb=checkpoint_size_mb,
    )
    lora_mb = max(0, int(active_lora_count)) * LORA_ADAPTER_WEIGHT_MB
    vae_overhead = VAE_FP32_OVERHEAD_MB if hr_active else 0
    total_weights_mb = base_weights_mb + lora_mb + vae_overhead

    # 1. Буфер базового холста (активации UNet + декодирование VAE)
    clean_w = max(64, int(width or (1024 if is_xl else 512)))
    clean_h = max(64, int(height or (1024 if is_xl else 768)))
    base_mpix = (clean_w * clean_h) / 1_000_000.0

    if is_xl:
        base_canvas_mb = int(BASE_CANVAS_FIXED_SDXL_MB + base_mpix * BASE_CANVAS_PER_MPIX_SDXL_MB)
    else:
        base_canvas_mb = int(BASE_CANVAS_FIXED_SD15_MB + base_mpix * BASE_CANVAS_PER_MPIX_SD15_MB)

    # 2. Этап Hires.fix
    if hr_active:
        hr_w = int(round(clean_w * eff_scale))
        hr_h = int(round(clean_h * eff_scale))
    else:
        hr_w, hr_h = clean_w, clean_h

    # Авто-тайлинг VAE вычисляется ДО расчета hr_stage_mb и est_peak_mb,
    # чтобы при hr_vae_tile_needed применялся реальный tiling_factor (0.65)
    hr_vae_tile_needed = (
        (is_xl and max(hr_w, hr_h) >= 1024)
        or (not is_xl and (hr_w * hr_h) > 1_300_000)
    ) if hr_active else False

    enable_vae_tiling = bool(
        vae_tiling
        or hr_vae_tile_needed
        or total_vram_mb <= 6144
    )

    if hr_active:
        hr_mpix = (hr_w * hr_h) / 1_000_000.0
        upscaler_mb = 0 if "latent" in str(hr_upscaler or "Latent").lower() else 220
        tiling_factor = VAE_TILING_FACTOR if enable_vae_tiling else 1.0
        per_mpix = HIRES_PER_MPIX_SDXL_MB if is_xl else HIRES_PER_MPIX_SD15_MB
        hr_stage_mb = base_canvas_mb + upscaler_mb + int(hr_mpix * per_mpix * tiling_factor)
    else:
        hr_stage_mb = base_canvas_mb

    # 3. Этап ADetailer (YOLO детекторы + буфер инпейнта)
    if enable_adetailer:
        inpaint_sz = max(512, int(ad_inpaint_size or 512))
        yolo_mb = 80 + (80 if eff_hand_ad else 0)
        if is_xl:
            inpaint_buf_mb = 1550 if inpaint_sz >= 768 else 1250
        else:
            inpaint_buf_mb = 650 if inpaint_sz >= 768 else 450
        ad_stage_mb = yolo_mb + inpaint_buf_mb
    else:
        ad_stage_mb = 0

    stage_overhead_mb = max(hr_stage_mb, ad_stage_mb)
    est_peak_mb = total_weights_mb + stage_overhead_mb

    # Защита от заниженного free_vram_mb, если модель Forge уже в VRAM
    if not forge_holds_vram and free_vram_mb is not None and free_vram_mb > 0:
        effective_usable_mb = min(usable_vram_mb, int(free_vram_mb))
    else:
        effective_usable_mb = usable_vram_mb

    safe_budget_mb = max(0, effective_usable_mb - DEFAULT_SAFETY_MARGIN_MB)
    stage_offload_required_mb = (
        int(total_weights_mb * 0.55) + DEFAULT_FORGE_RESERVE_MB + DEFAULT_SAFETY_MARGIN_MB
    )

    # Классификация режима
    is_heavy_xl_fp16 = bool(is_xl and not eff_is_fp8 and (hr_active or enable_adetailer))
    vram_too_tight_for_weights = effective_usable_mb < (total_weights_mb + 512)

    if vram_too_tight_for_weights:
        mode = "STAGE_OFFLOAD"
        always_offload = True
        inference_memory_mb = 1024
        can_run = effective_usable_mb >= stage_offload_required_mb
    elif est_peak_mb <= safe_budget_mb:
        mode = "FAST_KEEP_VRAM"
        always_offload = False
        # Для SDXL на крупных холстах (>= ~1024x1024) с ADetailer включаем tiled VAE
        # и снижаем inference_memory_mb до 512 МБ, чтобы Forge не выкидывал UNet/CLIP
        # из VRAM на пике декодирования VAE перед шагом ADetailer (Reuse 1 loaded models).
        if is_xl and enable_adetailer and ((clean_w * clean_h >= 960 * 960) or hr_active):
            enable_vae_tiling = True
            inference_memory_mb = 512
        else:
            inference_memory_mb = 768 if (is_xl or hr_active or enable_adetailer) else 640
        can_run = True
    elif is_heavy_xl_fp16 or est_peak_mb > (effective_usable_mb + 1200):
        # Для SDXL FP16 с HR/AD не раздуваем inference_memory при always_offload=False
        # (иначе базовые шаги тормозят от PCIe-свопа), а сразу включаем STAGE_OFFLOAD
        mode = "STAGE_OFFLOAD"
        always_offload = True
        inference_memory_mb = 1280 if (is_xl and hr_active) else 1024
        can_run = effective_usable_mb >= stage_offload_required_mb
    else:
        mode = "SMART_SWAP"
        always_offload = False
        inference_memory_mb = 1536 if (is_xl and (hr_active or enable_adetailer)) else 1152
        can_run = True

    # Безусловное включение VAE Tiling при STAGE_OFFLOAD, малом VRAM (<= 6 ГБ) или крупном Hires
    if (
        mode == "STAGE_OFFLOAD"
        or always_offload
        or vram_too_tight_for_weights
        or total_vram_mb <= 6144
        or hr_vae_tile_needed
    ):
        enable_vae_tiling = True

    ad_label = (
        "Face+Hands" if (enable_adetailer and eff_hand_ad)
        else ("Face" if enable_adetailer else "OFF")
    )

    return {
        "mode": mode,
        "can_run": can_run,
        "always_offload": always_offload,
        "inference_memory_mb": inference_memory_mb,
        "enable_vae_tiling": enable_vae_tiling,
        "host_ram_starved": host_ram_starved,
        "force_fp8_storage": force_fp8_storage,
        "disable_secondary_adetailer": disable_secondary_adetailer,
        "force_disable_hr": force_disable_hr,
        "effective_vram_mb": effective_usable_mb,
        "usable_vram_mb": usable_vram_mb,
        "safe_budget_mb": safe_budget_mb,
        "est_peak_mb": est_peak_mb,
        "model_mb": total_weights_mb,
        "canvas_w": hr_w if hr_active else clean_w,
        "canvas_h": hr_h if hr_active else clean_h,
        "enable_hr": hr_active,
        "ad_label": ad_label,
        "full_keep_required_mb": est_peak_mb,
        "stage_offload_required_mb": stage_offload_required_mb,
    }


def format_vram_planner_log(policy: Dict[str, Any]) -> str:
    """Форматирует единую строку телеметрии [VRAM-Planner] для логов ArtDirector и ForgeClient."""
    mode = policy.get("mode", "DEFAULT")
    est_peak = int(policy.get("est_peak_mb", 0))
    usable = int(policy.get("effective_vram_mb", policy.get("usable_vram_mb", 0)))
    model_mb = int(policy.get("model_mb", 0))
    cw = int(policy.get("canvas_w", 0))
    ch = int(policy.get("canvas_h", 0))
    hr_on = bool(policy.get("enable_hr", False))
    ad_label = policy.get("ad_label", "OFF")
    always_off = bool(policy.get("always_offload", False))
    inf_mem = int(policy.get("inference_memory_mb", 1024))
    vae_tile = bool(policy.get("enable_vae_tiling", False))
    tile_str = ", VAETile=True" if vae_tile else ""

    return (
        f"[VRAM-Planner] Mode={mode} | Est.Peak={est_peak}MB / Usable={usable}MB "
        f"(Model={model_mb}MB, Canvas={cw}x{ch}, HR={hr_on}, AD={ad_label}{tile_str}) "
        f"-> always_offload={always_off}, inf_mem={inf_mem}MB."
    )
