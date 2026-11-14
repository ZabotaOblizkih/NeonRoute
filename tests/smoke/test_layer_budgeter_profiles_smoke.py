"""
Smoke test suite for GPU Topology Auditor & GGUF Layer Offload Budgeter across 4 hardware profiles:
- 2 GB VRAM / 16 GB RAM (Pure Compute & Primary Display, clean and with background VRAM)
- 4 GB VRAM / 16 GB RAM (Balanced hybrid offload, compute headroom + Pre-flight RAM Guard check)
- 8 GB VRAM / 16 GB RAM (Pure Compute WDDM safe NGL 47 vs CTX 8k NGL 99, ProcessManager CLI flag, Primary Display >= 46, after Forge residual VRAM, hysteresis guard, backward compat)
- 16 GB VRAM / 32 GB RAM (NGL 99 for 12B, heavy 18 GB / 60 layers, 32B model with background VRAM)
"""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.layer_budgeter import LayerBudgeter
from services.process_manager import ProcessManager

GEMMA4_12B_META = {
    "total_layers": 48,
    "model_size_mb": 7039.43,
    "architecture": "gemma4",
    "is_moe": False,
    "context_length": 131072,
    "sliding_window": 1024,
    "sliding_window_pattern": [True, True, True, True, True, False] * 8,  # 40 SWA, 8 Global
    "head_count": 16,
    "head_count_kv": [8, 8, 8, 8, 8, 1] * 8,
    "key_length": 512,
    "value_length": 512,
    "vocab_size": 262144,
}


@pytest.fixture(autouse=True)
def isolate_models_cache(tmp_path, monkeypatch):
    """Изолирует models_cache.json во временную директорию на время тестов."""
    fake_cache_path = tmp_path / "models_cache.json"
    monkeypatch.setattr("services.layer_budgeter.MODELS_CACHE_PATH", fake_cache_path)


class TestProfile2GB:
    """1. 2 ГБ VRAM / 16 ГБ RAM: ngl == 0, can_run is True (безопасный fallback на CPU)."""

    def test_2gb_vram_pure_compute_cpu_fallback(self):
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=2048,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl == 0
            assert audit["final_ngl"] == 0
            assert can_run is True
            assert "OK" in reason

    def test_2gb_vram_primary_display_cpu_fallback(self):
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=2048,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl == 0
            assert audit["final_ngl"] == 0
            assert can_run is True

    def test_profile_2gb_with_background_vram(self):
        """При фоне 300 МБ ngl снижается до 2–3 (без пробоя 2048 МБ), при Primary Display (700 МБ) -> ngl == 0, can_run is True."""
        qwen_meta = {
            "total_layers": 28,
            "model_size_mb": 4480.0,
            "architecture": "qwen2",
            "is_moe": False,
            "context_length": 32768,
            "sliding_window": 0,
            "vocab_size": 152064,
        }
        # 1. Pure Compute с фоном 300 МБ -> ngl в пределах 2..3
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=qwen_meta):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_qwen.gguf",
                target_context=2048,
                total_vram_mb=2048,
                current_used_vram_mb=300.0,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl in (2, 3)
            assert can_run is True
            # Проверка отсутствия пробоя 2048 МБ
            total_allocated = 300 + 896 + (ngl * 128) + audit["kv_cache_mb"] + audit["compute_buffer_mb"]
            assert total_allocated <= 2048

        # 2. Primary Display с фоном 300 МБ (резерв ОС 700 МБ) -> ngl == 0, can_run is True
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=qwen_meta):

            ngl_pri, can_run_pri, _, _ = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_qwen.gguf",
                target_context=2048,
                total_vram_mb=2048,
                current_used_vram_mb=300.0,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl_pri == 0
            assert can_run_pri is True


class TestProfile4GB:
    """2. 4 ГБ VRAM / 16 ГБ RAM: сбалансированный гибридный оффлоад (10 <= ngl <= 22) и RAM Guard."""

    def test_4gb_vram_hybrid_offload_pure_compute(self):
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=4096,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert 10 <= ngl <= 22
            assert can_run is True
            assert audit["final_ngl"] == ngl

    def test_4gb_vram_hybrid_offload_primary_display(self):
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=4096,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl > 0
            assert can_run is True

    def test_profile_4gb_pure_and_primary_with_compute_headroom(self):
        """В Pure Compute ngl == 18, в Primary Display ngl == 14, фактическая сумма оставляет >= 300 МБ свободной VRAM."""
        qwen_meta = {
            "total_layers": 28,
            "model_size_mb": 4480.0,
            "architecture": "qwen2",
            "is_moe": False,
            "context_length": 32768,
            "sliding_window": 0,
            "vocab_size": 152064,
        }
        # 1. Pure Compute -> ngl == 18
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=qwen_meta):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_qwen.gguf",
                target_context=3584,
                total_vram_mb=4096,
                current_used_vram_mb=150.0,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl == 18
            assert can_run is True
            gpu_used = 150 + 896 + (18 * 128) + audit["kv_cache_mb"] + audit["compute_buffer_mb"]
            assert (4096 - gpu_used) >= 300

        # 2. Primary Display -> ngl == 14
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=qwen_meta):

            ngl_pri, can_run_pri, reason_pri, audit_pri = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_qwen.gguf",
                target_context=3584,
                total_vram_mb=4096,
                current_used_vram_mb=700.0,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl_pri == 14
            assert can_run_pri is True
            gpu_used_pri = 700 + 896 + (14 * 128) + audit_pri["kv_cache_mb"] + audit_pri["compute_buffer_mb"]
            assert (4096 - gpu_used_pri) >= 300

    def test_4gb_vram_preflight_ram_guard_triggers_on_low_ram(self):
        """При недостатке RAM (4 ГБ) и вытеснении слоев на CPU срабатывает Pre-flight OOM Guard."""
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=4096,
                available_ram_mb=4096.0,  # Всего 4 ГБ доступной RAM
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert can_run is False
            assert ngl == 0
            assert "OOM Guard" in reason


class TestProfile8GB:
    """3. 8 ГБ VRAM / 16 ГБ RAM: Pure Compute WDDM safe NGL 47 vs CTX 8k NGL 99, ProcessManager CLI, Primary Display >= 46, backward compat."""

    def test_8gb_vram_pure_compute_wddm_safe_ngl_47_and_ctx8k_ngl_99(self):
        """В Pure Compute на 8151 МБ с фоном 241 МБ Gemma 4 на CTX=14336 дает ngl == 47, а на CTX=8192 дает ngl == 99."""
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            # 1. CTX=14336 -> 47 слоев (безопасный WDDM-зазор 300+ МБ, без ухода в PCIe Shared RAM)
            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=8151,
                current_used_vram_mb=241.0,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl == 47
            assert audit["final_ngl"] == 47
            assert audit["forge_reserve_mb"] == 0
            assert audit["safety_margin_mb"] == 280
            assert audit["compute_buffer_mb"] == 280  # 220 + 60
            assert can_run is True

            # 2. CTX=8192 -> 99 слоев (полный GPU оффлоад при меньшем KV-кэше)
            ngl_8k, can_run_8k, _, audit_8k = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=8192,
                total_vram_mb=8151,
                current_used_vram_mb=241.0,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl_8k == 99
            assert audit_8k["final_ngl"] == 99
            assert can_run_8k is True

    def test_profile_8gb_pure_compute_after_forge_residual_vram(self):
        """При остаточных 684.1 МБ VRAM от Forge Gemma 4 снижает оффлоад до ngl == 43 (не 99!)."""
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=8151,
                current_used_vram_mb=684.1,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl == 43
            assert audit["final_ngl"] == 43
            assert can_run is True

    def test_hysteresis_does_not_override_vram_deficit(self):
        """При current_running_ngl=99 (или 46) и used_vram=684.1 МБ гистерезис не форсирует 99, оставляя ngl == 43."""
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=8151,
                current_used_vram_mb=684.1,
                current_running_ngl=99,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl == 43
            assert audit["final_ngl"] == 43
            assert audit["hysteresis_applied"] is False
            assert can_run is True

    def test_8gb_vram_process_manager_generates_ngl_99(self):
        """ProcessManager при запуске llama-server.exe на CTX=8192 формирует флаг -ngl 99."""
        pm = ProcessManager()
        captured_cmd = []

        def fake_popen(cmd, *args, **kwargs):
            captured_cmd.extend(cmd)
            raise RuntimeError("Intercepted Popen for inspection")

        with patch("services.process_manager.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")), \
             patch("services.process_manager.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/dummy_gemma4.gguf")), \
             patch("services.process_manager.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.process_manager.ServerConfigResolver.resolve_cuda_dir", return_value=None), \
             patch("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", return_value=(True, 150)), \
             patch("services.layer_budgeter.LayerBudgeter.get_current_used_vram_mb", return_value=150.0), \
             patch("services.layer_budgeter.LayerBudgeter.parse_gguf_metadata", return_value=GEMMA4_12B_META), \
             patch("utils.system_monitor.SystemMonitor.get_total_vram_mb", return_value=8151), \
             patch("subprocess.Popen", side_effect=fake_popen), \
             patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
             patch("pathlib.Path.exists", return_value=True):

            pm.start_llama_server_sync(model_identifier="dummy_gemma4", context_length=8192)

            assert "-ngl" in captured_cmd
            ngl_idx = captured_cmd.index("-ngl")
            assert captured_cmd[ngl_idx + 1] == "99"

    def test_8gb_vram_primary_display_offload_ngl_safe_43(self):
        """В Primary Display (8192 МБ, OS reserve 700 МБ) модель 12B с учетом WDDM запаса и буфера логитов оффлоадит ngl == 43."""
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=8192,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl >= 43
            assert can_run is True

    def test_8gb_vram_backward_compat_non_arbiter(self):
        """При is_arbiter_managed=False сохраняется вычитание FORGE_VRAM_RESERVE_MB (450 МБ)."""
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=8151,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=False,
            )

            assert audit["forge_reserve_mb"] == 450
            assert can_run is True


class TestProfile16GB:
    """4. 16 ГБ VRAM / 32 ГБ RAM: ngl == 99 для 12B, тяжелые модели и 32B с фоном."""

    def test_16gb_vram_12b_full_offload(self):
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                total_vram_mb=16384,
                available_ram_mb=32768.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl == 99
            assert can_run is True

    def test_16gb_vram_heavy_model_partial_offload(self):
        heavy_meta = {
            "total_layers": 60,
            "model_size_mb": 18432.0,  # 18 GB
            "architecture": "llama",
            "is_moe": False,
            "context_length": 16384,
            "sliding_window": 0,
            "vocab_size": 32000,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=heavy_meta):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_heavy.gguf",
                target_context=14336,
                total_vram_mb=16384,
                available_ram_mb=32768.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert 40 <= ngl <= 50
            assert can_run is True

    def test_profile_16gb_large_32b_with_background(self):
        """На 16 ГБ VRAM для 32B модели (19.5 ГБ) при фоне 1300 МБ ngl снижается с 43 до 40, удерживая запас под WDDM."""
        meta_32b = {
            "total_layers": 64,
            "model_size_mb": 19500.0,
            "architecture": "qwen2",
            "is_moe": False,
            "context_length": 32768,
            "sliding_window": 0,
            "vocab_size": 152064,
        }
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(False, 700)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta_32b):

            # Чистый старт (фон 700 МБ) -> ngl == 43
            ngl_clean, _, _, audit_clean = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_32b.gguf",
                target_context=11136,
                total_vram_mb=16384,
                current_used_vram_mb=700.0,
                available_ram_mb=32768.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )
            assert ngl_clean == 43

            # С фоном 1300 МБ -> ngl снижается до 40
            ngl_bg, _, _, audit_bg = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_32b.gguf",
                target_context=11136,
                total_vram_mb=16384,
                current_used_vram_mb=1300.0,
                available_ram_mb=32768.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )
            assert ngl_bg == 40
            # Запас под WDDM
            used_gpu = 1300 + 3900 + (40 * 243.75) + audit_bg["kv_cache_mb"] + audit_bg["compute_buffer_mb"]
            assert (16384 - used_gpu) >= 300


class TestVocabSizeAndLMStudioReadiness:
    """5. Проверка инвалидации кэша GGUF при отсутствии vocab_size и логирования реального NGL в LMStudioClient."""

    def test_vocab_size_cache_invalidation_and_parsing(self, tmp_path):
        """При отсутствии vocab_size в кэше parse_gguf_metadata перечитывает файл и извлекает vocab_size."""
        import struct
        model_file = tmp_path / "gemma4_test.gguf"

        # Бинарный заголовок GGUF v3 с tokenizer.ggml.tokens (item_count = 262144)
        header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 2)
        arch_key = b"general.architecture"
        arch_val = b"gemma4"
        kv1 = struct.pack("<Q", len(arch_key)) + arch_key + struct.pack("<I", 8) + struct.pack("<Q", len(arch_val)) + arch_val
        tok_key = b"tokenizer.ggml.tokens"
        kv2 = struct.pack("<Q", len(tok_key)) + tok_key + struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", 262144)
        kv2 += struct.pack("<Q", 4) + b"<pad>"
        model_file.write_bytes(header + kv1 + kv2)

        # Создаем старый кэш БЕЗ vocab_size
        stat = model_file.stat()
        cache_key = f"{str(model_file.resolve())}::{stat.st_mtime}::{stat.st_size}"
        stale_cache = {cache_key: {"total_layers": 48, "model_size_mb": 7039.43, "sliding_window": 1024}}
        LayerBudgeter._write_models_cache(stale_cache)

        # Чтение метаданных должно проигнорировать старый кэш, перечитать файл и извлечь 262144
        meta = LayerBudgeter.parse_gguf_metadata(model_file)
        assert meta.get("vocab_size") == 262144

        # Кэш на диске должен обновиться
        new_cache = LayerBudgeter._read_models_cache()
        assert new_cache[cache_key].get("vocab_size") == 262144

    def test_lm_studio_logs_effective_ngl_not_hardcoded_99(self, caplog):
        """LMStudioClient при старте логирует фактический effective_ngl (45/48), а не захардкоженные 99."""
        from services.lm_studio import LMStudioClient

        client = LMStudioClient()
        client.process_manager = MagicMock()
        client.process_manager.has_headless_server.return_value = True
        client.process_manager.start_llama_server_sync.return_value = True
        client.process_manager._current_running_ngl = 45
        client.process_manager._current_total_layers = 48
        client._is_model_unloaded = True

        with caplog.at_level("INFO", logger="NeonRoute.LMStudio"):
            ok = client.ensure_model_loaded(preferred_model="test-gemma", context_length=14336)
            assert ok is True

        assert "GPU layers: 45/48" in caplog.text
        assert "GPU layers: 99" not in caplog.text
