"""services/art_director/vram_policy.py

Чистый математический модуль для динамического расчета бюджета VRAM
и безопасного порога Hires.fix. Изолирован от Qt, ForgeClient,
SystemMonitor и Director (Pure Logic).
"""

import logging
from typing import Tuple, Union, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Константы по умолчанию
WEIGHTS_SD15_MB = 2048
WEIGHTS_SDXL_FP8_MB = 3584
WEIGHTS_SDXL_FP16_MB = 6758

DEFAULT_FORGE_RESERVE_MB = 450
DEFAULT_SAFETY_MARGIN_MB = 150
DEFAULT_OS_RESERVE_MB = 700
ADETAILER_RESERVE_MB = 650

K_ARCH_SD15 = 170_000  # px/GB при активном VAE Tiling
K_ARCH_SDXL = 95_000

LORA_ADAPTER_WEIGHT_MB = 120


def calculate_max_hires_canvas_area(
    architecture: str = "sd15",
    is_fp8: bool = False,
    total_vram_gb: float = 8.0,
    os_reserve_mb: int = DEFAULT_OS_RESERVE_MB,
    enable_adetailer: bool = False,
    active_lora_count: int = 0
) -> int:
    """Вычисляет максимальную допустимую площадь кадра (в пикселях) для Hires.fix.

    Параметры:
        architecture: Архитектура модели ('sd15', 'sdxl' и т.д.).
        is_fp8: Флаг квантования весов SDXL в FP8.
        total_vram_gb: Общий объем доступной видеопамяти GPU в ГБ.
        os_reserve_mb: Резерв памяти для ОС/монитора (МБ).
        enable_adetailer: Флаг использования ADetailer (+650 МБ резерва).
        active_lora_count: Количество активных LoRA-адаптеров (по 120 МБ на адаптер).

    Возвращает:
        Целочисленный лимит площади пикселей int(avail_gb * K), либо 0,
        если памяти недостаточно или Hires.fix заблокирован.
    """
    arch = str(architecture).strip().lower()
    is_xl = "xl" in arch

    if is_xl:
        base_weights_mb = WEIGHTS_SDXL_FP8_MB if is_fp8 else WEIGHTS_SDXL_FP16_MB
        k_arch = K_ARCH_SDXL
    else:
        base_weights_mb = WEIGHTS_SD15_MB
        k_arch = K_ARCH_SD15

    lora_mb = max(0, int(active_lora_count)) * LORA_ADAPTER_WEIGHT_MB
    total_weights_mb = base_weights_mb + lora_mb

    total_reserve_mb = (
        int(os_reserve_mb)
        + DEFAULT_FORGE_RESERVE_MB
        + DEFAULT_SAFETY_MARGIN_MB
        + (ADETAILER_RESERVE_MB if enable_adetailer else 0)
    )

    total_vram_mb = float(total_vram_gb) * 1024.0
    used_mb = total_reserve_mb + total_weights_mb
    avail_mb = total_vram_mb - used_mb
    avail_gb = avail_mb / 1024.0

    if avail_gb <= 0:
        return 0

    # Если SDXL и доступная динамическая память < 4.5 GB -> Hires заблокирован
    if is_xl and avail_gb < 4.5:
        return 0

    return int(avail_gb * k_arch)


def is_hires_safe(
    width: int,
    height: int,
    architecture: str = "sd15",
    is_fp8: bool = False,
    total_vram_gb: float = 8.0,
    os_reserve_mb: int = DEFAULT_OS_RESERVE_MB,
    enable_adetailer: bool = False,
    active_lora_count: int = 0
) -> bool:
    """Определяет, безопасна ли генерация заданного разрешения с Hires.fix.

    Учитывает:
    - Защиту длинной стороны (max(w, h) > 1280 при VRAM <= 8 GB).
    - Штраф за экстремальные соотношения сторон (Aspect Ratio Penalty).
    - Динамический бюджет VRAM с учетом архитектуры, резервов и LoRA.
    """
    if width <= 0 or height <= 0:
        return False

    # Защита длинной стороны при малом объеме памяти
    if total_vram_gb <= 8.0 and max(width, height) > 1280:
        return False

    max_allowed_area = calculate_max_hires_canvas_area(
        architecture=architecture,
        is_fp8=is_fp8,
        total_vram_gb=total_vram_gb,
        os_reserve_mb=os_reserve_mb,
        enable_adetailer=enable_adetailer,
        active_lora_count=active_lora_count
    )

    if max_allowed_area <= 0:
        return False

    base_area = width * height
    ratio = max(width / height, height / width)

    # Защита от экстремальных соотношений (Aspect Ratio Penalty)
    if ratio > 1.5:
        penalty_factor = 1.0 + (ratio - 1.5) * 0.3
        effective_area = base_area * penalty_factor
    else:
        effective_area = float(base_area)

    return effective_area <= max_allowed_area


def resolve_hardware_vram_budget(system_monitor=None) -> Tuple[float, int]:
    """Безопасный адаптер к метрикам оборудования (SSoT).

    Возвращает (total_vram_gb, os_reserve_mb).
    Гарантирует 100% отказоустойчивость (Safe Fallback) при сбоях NVML/CI/CPU.
    """
    total_vram_gb = 8.0
    os_reserve_mb = DEFAULT_OS_RESERVE_MB

    # 1. Считывание топологии дисплея из LayerBudgeter
    try:
        from services.layer_budgeter import LayerBudgeter
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
    except Exception as e:
        logger.debug(f"[VRAMPolicy] Fallback объема VRAM: {e}")

    return total_vram_gb, os_reserve_mb


def resolve_forge_offload_policy(
    width: int = 896,
    height: int = 1152,
    architecture: str = "sd15",
    is_fp8: bool = True,
    enable_adetailer: bool = True,
    active_lora_count: int = 0,
    system_monitor: Optional[Any] = None,
    free_vram_mb: Optional[int] = None,
) -> Dict[str, Any]:
    """Определяет режим удержания весов Forge в VRAM строго по константам vram_policy.py.

    - Если доступной VRAM достаточно для весов + резервов + буфера холста:
      возвращает mode='FAST_KEEP_VRAM', always_offload=False (без перезагрузки по PCIe между txt2img и ADetailer).
    - Если памяти недостаточно для полного удержания, но хватает для пошагового прогона:
      возвращает mode='STAGE_OFFLOAD', always_offload=True (режим выгрузки-загрузки между этапами).
    """
    total_vram_gb, os_reserve_mb = resolve_hardware_vram_budget(system_monitor)
    total_vram_mb = int(float(total_vram_gb) * 1024.0)

    if free_vram_mb is None and system_monitor is not None:
        try:
            free_vram_mb = system_monitor.get_free_vram_mb()
        except Exception:
            pass

    arch = str(architecture or "sd15").strip().lower()
    is_xl = "xl" in arch or any(m in arch for m in ("pony", "dmd", "insta", "mop"))

    if is_xl:
        base_weights_mb = WEIGHTS_SDXL_FP8_MB if is_fp8 else WEIGHTS_SDXL_FP16_MB
        k_arch = K_ARCH_SDXL
    else:
        base_weights_mb = WEIGHTS_SD15_MB
        k_arch = K_ARCH_SD15

    lora_mb = max(0, int(active_lora_count)) * LORA_ADAPTER_WEIGHT_MB
    total_weights_mb = base_weights_mb + lora_mb

    total_reserve_mb = (
        int(os_reserve_mb)
        + DEFAULT_FORGE_RESERVE_MB
        + DEFAULT_SAFETY_MARGIN_MB
        + (ADETAILER_RESERVE_MB if enable_adetailer else 0)
    )

    # Строго по формуле vram_policy (used_mb = total_reserve_mb + total_weights_mb) + буфер инференса Forge (1024 МБ)
    used_mb = total_reserve_mb + total_weights_mb
    full_keep_required_mb = used_mb + 1024
    # Минимальный бюджет для пошаговой выгрузки-загрузки (--always-offload-from-vram)
    stage_offload_required_mb = base_weights_mb + DEFAULT_FORGE_RESERVE_MB + DEFAULT_SAFETY_MARGIN_MB

    # Если измеренный Free VRAM после выгрузки LLM передан, учитываем резерв ОС и безопасности
    effective_vram_mb = (
        min(total_vram_mb, int(free_vram_mb) + int(os_reserve_mb))
        if (free_vram_mb is not None and free_vram_mb > 0)
        else total_vram_mb
    )

    if effective_vram_mb >= full_keep_required_mb:
        mode = "FAST_KEEP_VRAM"
        always_offload = False
        inference_memory_mb = 768 if is_xl else 640
        can_run = True
    elif effective_vram_mb >= stage_offload_required_mb:
        mode = "STAGE_OFFLOAD"
        always_offload = True
        inference_memory_mb = 1024
        can_run = True
    else:
        mode = "LOW_VRAM_GUARD"
        always_offload = True
        inference_memory_mb = 1024
        can_run = False

    return {
        "mode": mode,
        "can_run": can_run,
        "always_offload": always_offload,
        "inference_memory_mb": inference_memory_mb,
        "effective_vram_mb": effective_vram_mb,
        "full_keep_required_mb": full_keep_required_mb,
        "stage_offload_required_mb": stage_offload_required_mb,
    }
