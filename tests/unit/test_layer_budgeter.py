"""
tests/test_layer_budgeter.py
Модульные тесты для LayerBudgeter (топология GPU, парсинг GGUF, формула баланса слоев).
"""

import sys
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from config import (
    FORGE_VRAM_RESERVE_MB,
    SAFETY_MARGIN_VRAM_MB,
    OS_RESERVE_PURE_COMPUTE_MB,
    OS_RESERVE_PRIMARY_DISPLAY_MB,
    HYSTERESIS_LAYER_DELTA,
)
from services.layer_budgeter import LayerBudgeter


@pytest.fixture(autouse=True)
def isolate_models_cache(tmp_path, monkeypatch):
    """Изолирует data/models_cache.json на время тестов во временную директорию."""
    fake_cache_path = tmp_path / "models_cache.json"
    monkeypatch.setattr("services.layer_budgeter.MODELS_CACHE_PATH", fake_cache_path)


def create_mock_gguf_bytes(
    architecture: str = "llama",
    block_count: int = 32,
    context_length: int = 8192,
    is_moe: bool = False
) -> bytes:
    """Генерирует бинарный заголовок GGUF v3 для тестов."""
    kv_items = [
        ("general.architecture", architecture, 8),      # STRING
        (f"{architecture}.block_count", block_count, 4), # UINT32
        (f"{architecture}.context_length", context_length, 4), # UINT32
    ]
    if is_moe:
        kv_items.append((f"{architecture}.expert_count", 8, 4))

    out = bytearray()
    out.extend(b"GGUF")
    out.extend(struct.pack("<I", 3))   # version 3
    out.extend(struct.pack("<Q", 200)) # tensor_count
    out.extend(struct.pack("<Q", len(kv_items))) # kv_count

    for k, v, vt in kv_items:
        k_b = k.encode("utf-8")
        out.extend(struct.pack("<Q", len(k_b)))
        out.extend(k_b)
        out.extend(struct.pack("<I", vt))
        if vt == 8:
            v_b = v.encode("utf-8")
            out.extend(struct.pack("<Q", len(v_b)))
            out.extend(v_b)
        elif vt == 4:
            out.extend(struct.pack("<I", int(v)))

    return bytes(out)


class TestGPUTopologyDetection:
    """Проверка логики определения Pure Compute vs Primary Display через моки NVML."""

    def test_pure_compute_mode_when_low_vram_and_display_inactive(self):
        """Если занято < 250 МБ и nvmlDeviceGetDisplayActive == False -> PURE COMPUTE (150 МБ)."""
        mock_pynvml = MagicMock()
        mock_mem = MagicMock()
        mock_mem.used = 180 * 1024 * 1024  # 180 MB
        mock_pynvml.nvmlDeviceGetMemoryInfo.return_value = mock_mem
        mock_pynvml.nvmlDeviceGetDisplayActive.return_value = False

        with patch.dict(sys.modules, {"pynvml": mock_pynvml}):
            is_pure, reserve_mb = LayerBudgeter.detect_gpu_topology()
            assert is_pure is True
            assert reserve_mb == OS_RESERVE_PURE_COMPUTE_MB  # 150

    def test_primary_display_mode_when_display_active(self):
        """Если nvmlDeviceGetDisplayActive == True -> PRIMARY DISPLAY (700 МБ)."""
        mock_pynvml = MagicMock()
        mock_mem = MagicMock()
        mock_mem.used = 120 * 1024 * 1024  # 120 MB
        mock_pynvml.nvmlDeviceGetMemoryInfo.return_value = mock_mem
        mock_pynvml.nvmlDeviceGetDisplayActive.return_value = True

        with patch.dict(sys.modules, {"pynvml": mock_pynvml}):
            is_pure, reserve_mb = LayerBudgeter.detect_gpu_topology()
            assert is_pure is False
            assert reserve_mb == OS_RESERVE_PRIMARY_DISPLAY_MB  # 700

    def test_primary_display_mode_when_dwm_process_on_gpu(self):
        """Если на GPU обнаружен активный процесс dwm.exe с графическим дескриптором -> PRIMARY DISPLAY (700 МБ)."""
        mock_pynvml = MagicMock()
        mock_mem = MagicMock()
        mock_mem.used = 420 * 1024 * 1024  # 420 MB
        mock_pynvml.nvmlDeviceGetMemoryInfo.return_value = mock_mem
        mock_pynvml.nvmlDeviceGetDisplayActive.return_value = False
        mock_proc = MagicMock()
        mock_proc.pid = 1001
        mock_pynvml.nvmlDeviceGetGraphicsRunningProcesses.return_value = [mock_proc]
        mock_pynvml.nvmlSystemGetProcessName.return_value = b"dwm.exe"

        with patch.dict(sys.modules, {"pynvml": mock_pynvml}):
            is_pure, reserve_mb = LayerBudgeter.detect_gpu_topology()
            assert is_pure is False
            assert reserve_mb == OS_RESERVE_PRIMARY_DISPLAY_MB  # 700

    def test_nvml_failure_safe_fallback(self):
        """При исключении в pynvml происходит безопасный фоллбэк на (False, 700)."""
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlInit.side_effect = RuntimeError("NVML driver error")

        with patch.dict(sys.modules, {"pynvml": mock_pynvml}):
            is_pure, reserve_mb = LayerBudgeter.detect_gpu_topology()
            assert is_pure is False
            assert reserve_mb == 700


class TestGPUTopologyResidualVRAM:
    """При display_active = False и текущей VRAM = 650 МБ (остаточный кэш Forge) топология обязана определяться как PURE COMPUTE с резервом 150 МБ."""

    def test_residual_vram_pure_compute_mode(self):
        mock_pynvml = MagicMock()
        mock_mem = MagicMock()
        mock_mem.used = 650 * 1024 * 1024  # 650 MB остаточный кэш Forge
        mock_pynvml.nvmlDeviceGetMemoryInfo.return_value = mock_mem
        mock_pynvml.nvmlDeviceGetDisplayActive.return_value = False
        mock_pynvml.nvmlDeviceGetGraphicsRunningProcesses.return_value = []

        with patch.dict(sys.modules, {"pynvml": mock_pynvml}):
            is_pure, reserve_mb = LayerBudgeter.detect_gpu_topology()
            assert is_pure is True
            assert reserve_mb == OS_RESERVE_PURE_COMPUTE_MB  # 150


class TestGGUFMetadataParser:
    """Проверка чтения GGUF-метаданных, эвристического фоллбэка и кэширования."""

    def test_parse_valid_gguf_header(self, tmp_path):
        """Парсинг валидного заголовка GGUF v3."""
        model_file = tmp_path / "test_model_q4.gguf"
        data = create_mock_gguf_bytes(architecture="qwen2", block_count=36, context_length=32768)
        model_file.write_bytes(data)

        meta = LayerBudgeter.parse_gguf_metadata(model_file)
        assert meta["total_layers"] == 36
        assert meta["architecture"] == "qwen2"
        assert meta["context_length"] == 32768
        assert meta["is_moe"] is False

    def test_heuristic_fallback_small_model(self, tmp_path):
        """Нестандартный файл <= 5 ГБ -> эвристика 28 слоев."""
        model_file = tmp_path / "corrupt_small.gguf"
        model_file.write_bytes(b"INVALID_HEADER_DATA" * 50)

        # Мокируем размер 4.2 ГБ
        mock_stat = MagicMock()
        mock_stat.st_size = int(4.2 * (1024 ** 3))
        mock_stat.st_mtime = 1710000000.0
        mock_stat.st_mode = 0o100644

        with patch.object(Path, "stat", return_value=mock_stat):
            meta = LayerBudgeter.parse_gguf_metadata(model_file)
            assert meta["total_layers"] == 28

    def test_heuristic_fallback_medium_model(self, tmp_path):
        """Нестандартный файл 5–9 ГБ -> эвристика 40 слоев."""
        model_file = tmp_path / "corrupt_med.gguf"
        model_file.write_bytes(b"CORRUPT" * 20)

        mock_stat = MagicMock()
        mock_stat.st_size = int(7.5 * (1024 ** 3))
        mock_stat.st_mtime = 1710000001.0
        mock_stat.st_mode = 0o100644

        with patch.object(Path, "stat", return_value=mock_stat):
            meta = LayerBudgeter.parse_gguf_metadata(model_file)
            assert meta["total_layers"] == 40

    def test_heuristic_fallback_large_model(self, tmp_path):
        """Нестандартный файл > 9 ГБ -> эвристика 48 слоев."""
        model_file = tmp_path / "corrupt_large.gguf"
        model_file.write_bytes(b"CORRUPT" * 20)

        mock_stat = MagicMock()
        mock_stat.st_size = int(14.0 * (1024 ** 3))
        mock_stat.st_mtime = 1710000002.0
        mock_stat.st_mode = 0o100644

        with patch.object(Path, "stat", return_value=mock_stat):
            meta = LayerBudgeter.parse_gguf_metadata(model_file)
            assert meta["total_layers"] == 48

    def test_caching_mechanism(self, tmp_path):
        """Проверка кэширования метаданных по mtime и size."""
        model_file = tmp_path / "cached_model.gguf"
        data = create_mock_gguf_bytes(architecture="llama", block_count=32)
        model_file.write_bytes(data)

        # Первый вызов читает файл и пишет в кэш
        meta1 = LayerBudgeter.parse_gguf_metadata(model_file)
        assert meta1["total_layers"] == 32

        # Подменяем файл на мусор без изменения mtime/size
        with patch.object(LayerBudgeter, "_parse_gguf_header") as mock_parse:
            meta2 = LayerBudgeter.parse_gguf_metadata(model_file)
            # Заголовок не парсится повторно благодаря кэшу
            mock_parse.assert_not_called()
            assert meta2["total_layers"] == 32


class TestFullGPUOffload:
    """Компактная модель (~5.4 ГБ, 28 слоев) при 8 ГБ VRAM в режиме Pure Compute -> ngl = 99."""

    def test_compact_model_full_gpu_offload(self, tmp_path):
        model_file = tmp_path / "compact_5.4gb.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 100)

        mock_meta = {
            "total_layers": 28,
            "model_size_mb": 5529.6,  # 5.4 GB
            "architecture": "llama",
            "is_moe": False,
            "context_length": 4096,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=8192,
                available_ram_mb=16384.0
            )

            assert ngl == 99
            assert can_run is True
            assert audit["raw_ngl"] == 99
            assert audit["available_for_blocks_mb"] >= audit["blocks_weight_mb"]


class TestHybridOffload:
    """Модель 8.5 ГБ (48 слоев) при 8 ГБ VRAM -> частичный оффлоад (32–36 слоев), can_run=True."""

    def test_medium_model_hybrid_offload(self, tmp_path):
        model_file = tmp_path / "model_8.5gb.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 100)

        mock_meta = {
            "total_layers": 48,
            "model_size_mb": 8704.0,  # 8.5 GB
            "architecture": "llama",
            "is_moe": False,
            "context_length": 8192,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=8192,
                available_ram_mb=16384.0
            )

            # Ожидаемый диапазон 32–36 слоев
            assert 32 <= ngl <= 36
            assert can_run is True
            assert audit["final_ngl"] == ngl


class TestSmallCPUOffloadRAMGuard:
    """При вытеснении на CPU 1-2 слоев (~200 МБ) и available_ram = 4500 МБ проверка обязана успешно проходить (can_run = True)."""

    def test_small_cpu_offload_ram_guard_passes(self, tmp_path):
        model_file = tmp_path / "meissa_7b.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 100)

        # Модель Meissa 7B Q5 (~5192.6 MB, 28 слоев, вес слоя ~148.36 MB)
        # Вытеснение 1 слоя на CPU (~148 MB <= 500 MB)
        mock_meta = {
            "total_layers": 28,
            "model_size_mb": 5192.6,
            "architecture": "llama",
            "is_moe": False,
            "context_length": 4096,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=6100,
                available_ram_mb=4500.0
            )

            assert can_run is True
            assert ngl in (26, 27)
            assert audit["cpu_weights_mb"] <= 500.0
            assert "OK" in reason


class TestOOMPreflightGuard:
    """Попытка запуска модели 35 ГБ при 16 ГБ RAM или 20 ГБ слоев при 5 ГБ RAM -> can_run=False."""

    def test_heavy_model_triggers_oom_guard(self, tmp_path):
        model_file = tmp_path / "heavy_35gb.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 100)

        mock_meta = {
            "total_layers": 64,
            "model_size_mb": 35840.0,  # 35 GB
            "architecture": "command-r",
            "is_moe": False,
            "context_length": 4096,
        }

        mock_vmem = MagicMock()
        mock_vmem.available = 16 * (1024 ** 3)  # 16 GB RAM

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta), \
             patch("psutil.virtual_memory", return_value=mock_vmem):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=8192,
                available_ram_mb=16384.0
            )

            assert can_run is False
            assert ngl == 0
            assert "OOM Guard" in reason

    def test_20gb_cpu_offload_with_5gb_ram_triggers_oom_guard(self, tmp_path):
        """При вытеснении на CPU 20 ГБ весов и available_ram = 5000 МБ проверка обязана возвращать can_run = False."""
        model_file = tmp_path / "model_25gb.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 100)

        mock_meta = {
            "total_layers": 50,
            "model_size_mb": 27000.0,
            "architecture": "llama",
            "is_moe": False,
            "context_length": 4096,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=8192,
                available_ram_mb=5000.0
            )

            assert can_run is False
            assert ngl == 0
            assert "OOM Guard" in reason
            assert audit["cpu_weights_mb"] >= 20000.0


class TestHysteresisDelta:
    """Дельта в 2 слоя сохраняет текущий запущенный ngl без изменений."""

    def test_hysteresis_preserves_running_ngl_within_delta(self, tmp_path):
        model_file = tmp_path / "model_hysteresis.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 100)

        mock_meta = {
            "total_layers": 48,
            "model_size_mb": 8704.0,
            "architecture": "llama",
            "is_moe": False,
            "context_length": 8192,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):

            # Допустим расчетный ngl = 33
            ngl_base, _, _, _ = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=8192,
                current_running_ngl=None,
                available_ram_mb=16384.0
            )

            # Передаем current_running_ngl с дельтой 2 (например 31 или 35)
            running_ngl = ngl_base - 2
            ngl_hys, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=8192,
                current_running_ngl=running_ngl,
                available_ram_mb=16384.0
            )

            assert ngl_hys == running_ngl
            assert audit["hysteresis_applied"] is True

    def test_hysteresis_breaks_on_larger_delta(self, tmp_path):
        """Дельта больше HYSTERESIS_LAYER_DELTA (3) обновляет ngl на расчетный."""
        model_file = tmp_path / "model_hysteresis_large.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 100)

        mock_meta = {
            "total_layers": 48,
            "model_size_mb": 8704.0,
            "architecture": "llama",
            "is_moe": False,
            "context_length": 8192,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):

            ngl_base, _, _, _ = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=8192,
                current_running_ngl=None,
                available_ram_mb=16384.0
            )

            # Дельта 5 слоев (> 3)
            running_ngl = ngl_base - 5
            ngl_res, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=8192,
                current_running_ngl=running_ngl,
                available_ram_mb=16384.0
            )

            assert ngl_res == ngl_base
            assert audit["hysteresis_applied"] is False


class TestSafetyAndForgePreservation:
    """Проверка, что из бюджета всегда строго вычитаются 450 МБ Forge и 150 МБ буфера."""

    def test_strict_forge_and_safety_deduction(self, tmp_path):
        model_file = tmp_path / "test_safety.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 100)

        mock_meta = {
            "total_layers": 32,
            "model_size_mb": 6000.0,
            "architecture": "llama",
            "is_moe": False,
            "context_length": 4096,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):

            total_vram = 8192
            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path=model_file,
                target_context=4096,
                total_vram_mb=total_vram
            )

            assert audit["forge_reserve_mb"] == FORGE_VRAM_RESERVE_MB  # 450
            assert audit["safety_margin_mb"] == SAFETY_MARGIN_VRAM_MB  # 150

            expected_usable = (
                total_vram
                - audit["os_reserve_mb"]
                - FORGE_VRAM_RESERVE_MB
                - SAFETY_MARGIN_VRAM_MB
                - audit["kv_cache_mb"]
            )
            assert audit["usable_vram_mb"] == expected_usable


def _pack_gguf_str(s: str) -> bytes:
    encoded = s.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _build_test_gguf(kv_items: list) -> bytes:
    buf = bytearray()
    buf += b"GGUF"
    buf += struct.pack("<I", 3)
    buf += struct.pack("<Q", 0)
    buf += struct.pack("<Q", len(kv_items))
    for key, vtype, val in kv_items:
        buf += _pack_gguf_str(key)
        buf += struct.pack("<I", vtype)
        if vtype == 4:
            buf += struct.pack("<I", int(val))
        elif vtype == 8:
            buf += _pack_gguf_str(str(val))
        elif vtype == 9:
            item_type, items = val
            buf += struct.pack("<I", item_type)
            buf += struct.pack("<Q", len(items))
            for item in items:
                if item_type == 4:
                    buf += struct.pack("<I", int(item))
                elif item_type == 8:
                    buf += _pack_gguf_str(str(item))
    return bytes(buf)


class TestAnalyticalBudgetAndGGUFStreaming:
    """Аналитический расчёт compute_mb, KV cache (SWA / asymmetric) и стриминговый парсер GGUF."""

    def test_compute_mb_and_safety_margin_for_large_vocab(self):
        assert SAFETY_MARGIN_VRAM_MB == 170
        mock_meta = {
            "total_layers": 48,
            "model_size_mb": 6698.65,
            "architecture": "gemma",
            "is_moe": False,
            "context_length": 14336,
            "vocab_size": 262144,
        }
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):
            ngl, can_run, _, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma.gguf",
                target_context=14336,
                total_vram_mb=8151,
                is_arbiter_managed=True,
                current_used_vram_mb=682.1,
                available_ram_mb=16000.0,
            )
            assert can_run is True
            assert audit["compute_buffer_mb"] == 396
            assert audit["logits_buffer_mb"] == 256
            assert audit["ubatch_size"] == 256
            assert audit["safety_margin_mb"] == 170
            assert ngl == 40

    @pytest.mark.parametrize("vocab_size,model_mb,ubatch,exp_logits,exp_compute", [
        (32000, 7000.0, 256, 32, 172),
        (152064, 7000.0, 256, 149, 289),
        (32000, 4000.0, 256, 32, 132),
        (262144, 7000.0, 512, 512, 652),
    ])
    def test_analytical_compute_mb_across_vocabularies(self, vocab_size, model_mb, ubatch, exp_logits, exp_compute):
        meta = {"total_layers": 32, "model_size_mb": model_mb, "vocab_size": vocab_size}
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta):
            _, _, _, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy.gguf",
                target_context=8192,
                total_vram_mb=8192,
                is_arbiter_managed=True,
                current_used_vram_mb=200.0,
                ubatch_size=ubatch,
            )
            assert audit["logits_buffer_mb"] == exp_logits
            assert audit["compute_buffer_mb"] == exp_compute

    def test_analytical_estimate_kv_cache_mb_swa_and_asymmetric(self):
        meta_swa = {
            "total_layers": 48,
            "sliding_window": 1024,
            "sliding_window_pattern": [True] * 40 + [False] * 8,
            "head_count_kv": [8] * 40 + [1] * 8,
            "key_length": 512,
            "value_length": 512,
            "key_length_swa": 256,
            "value_length_swa": 256,
        }
        assert LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0", gguf_meta=meta_swa) == 431
        assert LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q4", gguf_meta=meta_swa) == 228
        assert LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0_q4_0", gguf_meta=meta_swa) == 329
        assert LayerBudgeter.estimate_kv_cache_mb(24576, cache_type="q8_0_q4_0", gguf_meta=meta_swa) == 404

        meta_no_dims = {"total_layers": 32, "sliding_window": 0}
        assert LayerBudgeter.estimate_kv_cache_mb(8192, cache_type="q8_0", gguf_meta=meta_no_dims) == 589

        meta_gemma4_3_5 = {
            "total_layers": 48,
            "head_count_kv": 3.5,
            "key_length": 256,
            "value_length": 256,
            "sliding_window": 1024,
        }
        assert LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0", gguf_meta=meta_gemma4_3_5) == 368
        assert LayerBudgeter.estimate_kv_cache_mb(14336, cache_type="q8_0", gguf_meta={**meta_gemma4_3_5, "sliding_window": 0}) == 1437

    def test_gemma4_offload_increases_with_ubatch_256(self):
        gemma4_meta = {
            "total_layers": 48,
            "model_size_mb": 6850.0,
            "architecture": "gemma4",
            "is_moe": False,
            "context_length": 131072,
            "sliding_window": 1024,
            "sliding_window_pattern": [True, True, True, True, True, False] * 8,
            "head_count": 16,
            "head_count_kv": [8, 8, 8, 8, 8, 1] * 8,
            "key_length": 256,
            "value_length": 256,
            "vocab_size": 262144,
        }
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=gemma4_meta):
            ngl_512, _, _, audit_512 = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf", target_context=14336, total_vram_mb=8151,
                current_used_vram_mb=553.0, available_ram_mb=16384.0, cache_type="q8_0",
                is_arbiter_managed=True, ubatch_size=512,
            )
            assert ngl_512 == 44
            assert audit_512["logits_buffer_mb"] == 512

            ngl_256, can_run_256, _, audit_256 = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf", target_context=14336, total_vram_mb=8151,
                current_used_vram_mb=553.0, available_ram_mb=16384.0, cache_type="q8_0",
                is_arbiter_managed=True, ubatch_size=256,
            )
            assert ngl_256 == 46
            assert can_run_256 is True
            assert audit_256["compute_buffer_mb"] == 396

    def test_gguf_streaming_parser_and_cache_invalidation(self, tmp_path, monkeypatch):
        large_tokens = ["token_" + ("x" * 200) for _ in range(500)]
        model_file = tmp_path / "streaming_test.gguf"
        model_file.write_bytes(_build_test_gguf([
            ("general.architecture", 8, "llama"),
            ("llama.block_count", 4, 32),
            ("tokenizer.ggml.tokens", 9, (8, large_tokens)),
            ("llama.attention.sliding_window", 4, 1024),
        ]))
        meta = LayerBudgeter.parse_gguf_metadata(model_file)
        assert meta["architecture"] == "llama"
        assert meta["total_layers"] == 32
        assert meta["sliding_window"] == 1024
        assert meta["vocab_size"] == 500
        assert meta["schema_version"] == 3

        arr_file = tmp_path / "array_swa_test.gguf"
        arr_file.write_bytes(_build_test_gguf([
            ("general.architecture", 8, "gemma2"),
            ("gemma2.block_count", 4, 6),
            ("gemma2.attention.sliding_window", 9, (4, [1024, 1024, 1024, 1024, 1024, 0])),
        ]))
        meta_arr = LayerBudgeter.parse_gguf_metadata(arr_file)
        assert meta_arr["sliding_window_pattern"] == [True, True, True, True, True, False]

        stat = arr_file.stat()
        cache_key = f"{str(arr_file.resolve())}::{stat.st_mtime}::{stat.st_size}"
        monkeypatch.setattr(LayerBudgeter, "_read_models_cache", classmethod(lambda cls: {
            cache_key: {"schema_version": 1, "total_layers": 999, "sliding_window": 0}
        }))
        written = {}
        monkeypatch.setattr(LayerBudgeter, "_write_models_cache", classmethod(lambda cls, c: written.update(c)))
        meta_re = LayerBudgeter.parse_gguf_metadata(arr_file)
        assert meta_re["total_layers"] == 6
        assert written[cache_key]["schema_version"] == 3

