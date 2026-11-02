"""tests/unit/test_vram_policy.py

Модульные тесты для чистой математической политики vram_policy.py.
"""

from unittest.mock import MagicMock, patch
import pytest

from services.art_director.vram_policy import (
    calculate_max_hires_canvas_area,
    is_hires_safe,
    resolve_hardware_vram_budget,
    WEIGHTS_SD15_MB,
    WEIGHTS_SDXL_FP8_MB,
    WEIGHTS_SDXL_FP16_MB,
    DEFAULT_FORGE_RESERVE_MB,
    DEFAULT_SAFETY_MARGIN_MB,
    DEFAULT_OS_RESERVE_MB,
    ADETAILER_RESERVE_MB,
    K_ARCH_SD15,
    K_ARCH_SDXL,
)


def test_sd15_pure_compute_allows_768x1024():
    """8 GB, резерв 150 MB (iGPU) -> лимит ≈ 895k px, кадр 768x1024 разрешен."""
    limit = calculate_max_hires_canvas_area(
        architecture="sd15",
        total_vram_gb=8.0,
        os_reserve_mb=150,
        enable_adetailer=False,
        active_lora_count=0
    )
    # 8192 - (150 + 450 + 150 + 2048) = 5394 MB -> 5.267578 GB * 170000 = 895488 px
    assert 894000 <= limit <= 896000
    assert is_hires_safe(
        768, 1024,
        architecture="sd15",
        total_vram_gb=8.0,
        os_reserve_mb=150,
        enable_adetailer=False
    ) is True


def test_sd15_primary_display_allows_768x1024():
    """8 GB, резерв 700 MB (dGPU) -> лимит ≈ 804k px, кадр 768x1024 разрешен."""
    limit = calculate_max_hires_canvas_area(
        architecture="sd15",
        total_vram_gb=8.0,
        os_reserve_mb=700,
        enable_adetailer=False,
        active_lora_count=0
    )
    # 8192 - (700 + 450 + 150 + 2048) = 4844 MB -> 4.730468 GB * 170000 = 804179 px
    assert 803000 <= limit <= 805000
    assert is_hires_safe(
        768, 1024,
        architecture="sd15",
        total_vram_gb=8.0,
        os_reserve_mb=700,
        enable_adetailer=False
    ) is True


def test_sd15_primary_display_blocks_1024x1024():
    """8 GB, dGPU -> холст 1024x1024 (1M px) заблокирован."""
    assert is_hires_safe(
        1024, 1024,
        architecture="sd15",
        total_vram_gb=8.0,
        os_reserve_mb=700,
        enable_adetailer=False
    ) is False


def test_sdxl_blocked_on_8gb():
    """SDXL на 8 GB возвращает 0 и is_hires_safe == False."""
    limit_fp16 = calculate_max_hires_canvas_area(
        architecture="sdxl",
        is_fp8=False,
        total_vram_gb=8.0
    )
    assert limit_fp16 == 0
    assert is_hires_safe(1024, 1024, architecture="sdxl", is_fp8=False, total_vram_gb=8.0) is False

    limit_fp8 = calculate_max_hires_canvas_area(
        architecture="sdxl",
        is_fp8=True,
        total_vram_gb=8.0
    )
    assert limit_fp8 == 0
    assert is_hires_safe(1024, 1024, architecture="sdxl", is_fp8=True, total_vram_gb=8.0) is False


def test_sdxl_fp8_allowed_on_16gb():
    """На 16 GB GPU базовый кадр 1024x1024 разрешает Hires."""
    limit = calculate_max_hires_canvas_area(
        architecture="sdxl",
        is_fp8=True,
        total_vram_gb=16.0
    )
    assert limit > 1024 * 1024
    assert is_hires_safe(
        1024, 1024,
        architecture="sdxl",
        is_fp8=True,
        total_vram_gb=16.0
    ) is True


def test_aspect_ratio_penalty():
    """Сравнение 896x896 vs 448x1792 при одинаковом числе пикселей (вытянутый кадр получает пенальти)."""
    # 896 * 896 = 802816 px (ratio = 1.0, без штрафа)
    # 448 * 1792 = 802816 px (ratio = 4.0, штраф 1.0 + 2.5 * 0.3 = 1.75 -> 1404928 px)
    # На 8 GB GPU (лимит ≈ 804k px):
    # 896x896 укладывается (802816 <= 804179 и max_side 896 <= 1280)
    assert is_hires_safe(896, 896, architecture="sd15", total_vram_gb=8.0, os_reserve_mb=700) is True
    # 448x1792 блокируется как пенальти, так и защитой длинной стороны (> 1280)
    assert is_hires_safe(448, 1792, architecture="sd15", total_vram_gb=8.0, os_reserve_mb=700) is False

    # Также проверим на 16 GB GPU (где лимит > 1.4M и ограничение длинной стороны 1280 не срабатывает):
    # При total_vram_gb=12.0: лимит = int((12288 - 3348)/1024 * 170000) = 1484179 px
    # 896x896 effective_area = 802816 <= 1484179 -> True
    # 448x1792: ratio 4.0, penalty factor 1.75, effective_area = 1404928
    # Если уменьшить VRAM до 11 GB:
    limit_11gb = calculate_max_hires_canvas_area("sd15", total_vram_gb=11.0, os_reserve_mb=700)
    # avail = 11264 - 3348 = 7916 MB -> 7.730468 GB -> limit = 1314179 px
    # 802816 <= 1314179 (квадрат разрешен), но 1404928 > 1314179 (вытянутый заблокирован по эффективной площади)
    assert is_hires_safe(896, 896, architecture="sd15", total_vram_gb=11.0, os_reserve_mb=700) is True
    assert is_hires_safe(448, 1792, architecture="sd15", total_vram_gb=11.0, os_reserve_mb=700) is False


def test_adetailer_reduces_limit():
    """Проверка уменьшения лимита при enable_adetailer=True."""
    limit_without_ad = calculate_max_hires_canvas_area(
        architecture="sd15",
        total_vram_gb=8.0,
        enable_adetailer=False
    )
    limit_with_ad = calculate_max_hires_canvas_area(
        architecture="sd15",
        total_vram_gb=8.0,
        enable_adetailer=True
    )
    assert limit_with_ad < limit_without_ad
    # Разница в площади должна соответствовать (ADETAILER_RESERVE_MB / 1024) * 170000
    expected_diff = int((ADETAILER_RESERVE_MB / 1024.0) * K_ARCH_SD15)
    assert abs((limit_without_ad - limit_with_ad) - expected_diff) <= 2


def test_resolve_hardware_vram_budget_fallback():
    """При вызове без параметров или при ошибке импорта возвращает (8.0, 700)."""
    with patch("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", side_effect=RuntimeError("Topology fail")):
        vram_gb, reserve_mb = resolve_hardware_vram_budget(system_monitor=None)
        assert vram_gb == 8.0
        assert reserve_mb == 700


def test_resolve_hardware_vram_budget_pure_compute():
    """При моке LayerBudgeter.detect_gpu_topology в (True, 150) возвращает os_reserve_mb=150."""
    with patch("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", return_value=(True, 150)):
        vram_gb, reserve_mb = resolve_hardware_vram_budget(system_monitor=None)
        assert vram_gb == 8.0
        assert reserve_mb == 150


def test_resolve_hardware_vram_budget_reads_monitor_stats():
    """При передаче system_monitor со статами vram_total_mb=12288 возвращает total_vram_gb=12.0."""
    mock_monitor = MagicMock()
    mock_monitor._last_emitted_stats = {"vram_total_mb": 12288}
    with patch("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", return_value=(False, 700)):
        vram_gb, reserve_mb = resolve_hardware_vram_budget(system_monitor=mock_monitor)
        assert vram_gb == 12.0
        assert reserve_mb == 700
