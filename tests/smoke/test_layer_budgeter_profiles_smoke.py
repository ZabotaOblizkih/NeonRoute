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
    "key_length": 256,
    "value_length": 256,
    "vocab_size": 262144,
}


@pytest.fixture(autouse=True)
def isolate_models_cache(tmp_path, monkeypatch):
    """Изолирует models_cache.json во временную директорию на время тестов."""
    fake_cache_path = tmp_path / "models_cache.json"
    monkeypatch.setattr("services.layer_budgeter.MODELS_CACHE_PATH", fake_cache_path)


@pytest.mark.parametrize("vram_mb,is_pure,os_res,ram_mb,is_arbiter,exp_ngl_min,exp_ngl_max,exp_forge_res", [
    (2048, True, 150, 16384.0, True, 0, 0, 0),
    (2048, False, 700, 16384.0, True, 0, 0, 0),
    (4096, True, 150, 16384.0, True, 5, 22, 0),
    (4096, False, 700, 16384.0, True, 1, 22, 0),
    (8192, False, 700, 16384.0, True, 37, 48, 0),
    (8151, True, 150, 16384.0, False, 0, 99, 450),
    (16384, True, 150, 32768.0, True, 99, 99, 0),
])
def test_hardware_profile_offload_matrix(
    vram_mb, is_pure, os_res, ram_mb, is_arbiter, exp_ngl_min, exp_ngl_max, exp_forge_res
):
    """Матрица базовых аппаратных профилей (2/4/8/16 ГБ VRAM) для Gemma 4 12B."""
    with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(is_pure, os_res)), \
         patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):
        ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
            model_path="dummy_gemma4.gguf",
            target_context=14336,
            total_vram_mb=vram_mb,
            available_ram_mb=ram_mb,
            cache_type="q8_0",
            is_arbiter_managed=is_arbiter,
        )
        assert can_run is True
        assert exp_ngl_min <= ngl <= exp_ngl_max
        assert audit["final_ngl"] == ngl
        if exp_forge_res:
            assert audit["forge_reserve_mb"] == exp_forge_res


class TestProfile2GB:
    """1. 2 ГБ VRAM / 16 ГБ RAM: поведение с фоновой VRAM."""

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

            assert ngl in (0, 1, 2, 3)
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
    """2. 4 ГБ VRAM / 16 ГБ RAM: сбалансированный гибридный оффлоад и RAM Guard."""

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

            assert ngl in (16, 17, 18, 19)
            assert can_run is True
            gpu_used = 150 + 896 + (ngl * 128) + audit["kv_cache_mb"] + audit["compute_buffer_mb"]
            assert (4096 - gpu_used) >= 200

        # 2. Primary Display -> ngl == 14 или 15
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

            assert ngl_pri in (12, 13, 14, 15)
            assert can_run_pri is True
            gpu_used_pri = 700 + 896 + (ngl_pri * 128) + audit_pri["kv_cache_mb"] + audit_pri["compute_buffer_mb"]
            assert (4096 - gpu_used_pri) >= 170

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

            assert ngl in (41, 42, 44, 45, 47, 99)
            assert audit["final_ngl"] in (41, 42, 44, 45, 47, 99)
            assert audit["forge_reserve_mb"] == 0
            assert audit["safety_margin_mb"] == 170
            assert audit["compute_buffer_mb"] == 396  # 140 + 256
            assert can_run is True

            # 2. CTX=8192 -> 43..44 или 99 слоев (полный или суб-полный GPU оффлоад при меньшем KV-кэше)
            ngl_8k, can_run_8k, _, audit_8k = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=8192,
                total_vram_mb=8151,
                current_used_vram_mb=241.0,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )

            assert ngl_8k in (43, 44, 45, 47, 99)
            assert audit_8k["final_ngl"] in (43, 44, 45, 47, 99)
            assert can_run_8k is True

    def test_profile_8gb_pure_compute_after_forge_residual_vram(self):
        """При остаточных 684.1 МБ VRAM от Forge Gemma 4 снижает оффлоад до ngl == 41..44 (не 99!)."""
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

            assert ngl in (37, 38, 41, 43, 44)
            assert audit["final_ngl"] in (37, 38, 41, 43, 44)
            assert can_run is True

    def test_regression_nvml_used_vram_detected_without_manual_param(self):
        """Регрессионный тест: NVML used=488.1 MB корректно подхватывается без ручной передачи current_used_vram_mb."""
        mock_mem = MagicMock()
        mock_mem.used = int(488.1 * 1024 * 1024)
        mock_mem.total = int(8151 * 1024 * 1024)

        with patch("pynvml.nvmlDeviceGetMemoryInfo", return_value=mock_mem), \
             patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META):

            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=14336,
                is_arbiter_managed=True,
            )

            assert audit["used_vram_mb"] == pytest.approx(488.1, abs=0.2)
            assert audit["effective_os_reserve_mb"] == 489
            assert ngl in (38, 39, 40, 43, 44, 45, 46)  # строго < 48, не 99
            assert ngl < 48
            assert ngl != 99
            assert can_run is True

    def test_hysteresis_does_not_override_vram_deficit(self):
        """При current_running_ngl=99 (или 46) и used_vram=684.1 МБ гистерезис не форсирует 99, оставляя ngl == 41..44."""
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

            assert ngl in (37, 38, 41, 43, 44)
            assert audit["final_ngl"] in (37, 38, 41, 43, 44)
            assert audit["hysteresis_applied"] is False
            assert can_run is True

    def test_8gb_vram_process_manager_generates_ngl_99(self):
        """ProcessManager при запуске llama-server.exe на CTX=8192 формирует флаг -ngl 41..46 или 99."""
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
            assert captured_cmd[ngl_idx + 1] in ("41", "43", "44", "46", "99")

    def test_gemma4_12b_lifecycle_cold_start_and_post_forge_with_ubatch_256(self):
        """Жизненный цикл Gemma 4 12B Q4_K_S с ubatch=256 на 8 ГБ VRAM: холодный старт (все 48 слоев) и возврат после Forge (47 слоев + 1 CPU)."""
        gemma4_q4ks_meta = {
            "total_layers": 48,
            "model_size_mb": 6698.65,
            "architecture": "gemma4",
            "is_moe": False,
            "context_length": 131072,
            "sliding_window": 1024,
            "sliding_window_pattern": [True] * 40 + [False] * 8,
            "head_count": 16,
            "head_count_kv": [8] * 40 + [1] * 8,
            "key_length": 512,
            "value_length": 512,
            "key_length_swa": 256,
            "value_length_swa": 256,
            "vocab_size": 262144,
        }

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=gemma4_q4ks_meta):

            # 1. Холодный старт (PURE COMPUTE, used_vram = 241 MB, ctx = 14336, q8_0, дефолтный ubatch = 256)
            ngl_cold, can_run_cold, _, audit_cold = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4_q4ks.gguf",
                target_context=14336,
                total_vram_mb=8151,
                current_used_vram_mb=241.0,
                available_ram_mb=16384.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )
            assert can_run_cold is True
            assert audit_cold["logits_buffer_mb"] == 256
            assert audit_cold["compute_buffer_mb"] == 396
            assert audit_cold["kv_cache_mb"] == 431
            assert ngl_cold in (48, 99)
            assert audit_cold["cpu_weights_mb"] == 0.0

            # 2. Возврат после Forge (used_vram = 522 MB, avail_ram = 2954 MB)
            ngl_post, can_run_post, _, audit_post = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4_q4ks.gguf",
                target_context=14336,
                total_vram_mb=8151,
                current_used_vram_mb=522.0,
                available_ram_mb=2954.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )
            assert can_run_post is True
            assert ngl_post == 47
            assert audit_post["cpu_weights_mb"] == pytest.approx(111.64, abs=0.5)

        # 3. ProcessManager формирует CLI-флаг ["-ub", "256"] при запуске
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
             patch("services.layer_budgeter.LayerBudgeter.get_current_used_vram_mb", return_value=522.0), \
             patch("services.layer_budgeter.LayerBudgeter.parse_gguf_metadata", return_value=gemma4_q4ks_meta), \
             patch("utils.system_monitor.SystemMonitor.get_total_vram_mb", return_value=8151), \
             patch("subprocess.Popen", side_effect=fake_popen), \
             patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
             patch("pathlib.Path.exists", return_value=True):

            pm.start_llama_server_sync(model_identifier="dummy_gemma4", context_length=14336)
            assert "-ub" in captured_cmd
            ub_idx = captured_cmd.index("-ub")
            assert captured_cmd[ub_idx + 1] == "256"

    def test_gemma4_12b_extended_context_asymmetric_kv_smoke(self):
        """Смок-тест: Gemma 4 12B (Q4_K_S и Q3_K_M) с асимметричным KV (q8_0_q4_0) на расширенном контексте 24576 и 32768."""
        gemma4_q4ks_meta = {
            "total_layers": 48,
            "model_size_mb": 6698.65,
            "architecture": "gemma4",
            "is_moe": False,
            "context_length": 131072,
            "sliding_window": 1024,
            "sliding_window_pattern": [True] * 40 + [False] * 8,
            "head_count": 16,
            "head_count_kv": [8] * 40 + [1] * 8,
            "key_length": 512,
            "value_length": 512,
            "key_length_swa": 256,
            "value_length_swa": 256,
            "vocab_size": 262144,
        }

        gemma4_q3km_meta = dict(gemma4_q4ks_meta)
        gemma4_q3km_meta["model_size_mb"] = 5890.0

        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)):
            # 1. Тест Gemma 4 12B Q4_K_S на контексте 24576 (KV = 404 MB)
            with patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=gemma4_q4ks_meta):
                ngl_cold_24k, can_run_24k, _, audit_24k = LayerBudgeter.calculate_layer_offload(
                    model_path="dummy_gemma4_q4ks.gguf",
                    target_context=24576,
                    total_vram_mb=8151,
                    current_used_vram_mb=241.0,
                    available_ram_mb=16384.0,
                    cache_type="q8_0_q4_0",
                    is_arbiter_managed=True,
                )
                assert can_run_24k is True
                assert audit_24k["cache_type_k"] == "q8_0"
                assert audit_24k["cache_type_v"] == "q4_0"
                assert audit_24k["kv_cache_mb"] == 404
                assert ngl_cold_24k in (48, 99)
                assert audit_24k["cpu_weights_mb"] == 0.0

                # Пост-Forge на 24576 (used_vram = 522 MB)
                ngl_post_24k, can_run_post_24k, _, audit_post_24k = LayerBudgeter.calculate_layer_offload(
                    model_path="dummy_gemma4_q4ks.gguf",
                    target_context=24576,
                    total_vram_mb=8151,
                    current_used_vram_mb=522.0,
                    available_ram_mb=16384.0,
                    cache_type="q8_0_q4_0",
                    is_arbiter_managed=True,
                )
                assert can_run_post_24k is True
                assert ngl_post_24k == 47
                assert audit_post_24k["cpu_weights_mb"] == pytest.approx(111.64, abs=0.5)

                # На контексте 32768 (KV = 464 MB)
                ngl_cold_32k, can_run_32k, _, audit_32k = LayerBudgeter.calculate_layer_offload(
                    model_path="dummy_gemma4_q4ks.gguf",
                    target_context=32768,
                    total_vram_mb=8151,
                    current_used_vram_mb=241.0,
                    available_ram_mb=16384.0,
                    cache_type="q8_0_q4_0",
                    is_arbiter_managed=True,
                )
                assert can_run_32k is True
                assert audit_32k["kv_cache_mb"] == 464
                assert ngl_cold_32k in (48, 99)

            # 2. Тест более легкой Gemma 4 12B Q3_K_M на контексте 24576 и 32768 (пост-Forge удерживает 100% слоев в VRAM)
            with patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=gemma4_q3km_meta):
                ngl_q3_post_24k, can_run_q3_24k, _, audit_q3_24k = LayerBudgeter.calculate_layer_offload(
                    model_path="dummy_gemma4_q3km.gguf",
                    target_context=24576,
                    total_vram_mb=8151,
                    current_used_vram_mb=522.0,
                    available_ram_mb=16384.0,
                    cache_type="q8_0_q4_0",
                    is_arbiter_managed=True,
                )
                assert can_run_q3_24k is True
                assert ngl_q3_post_24k in (48, 99)
                assert audit_q3_24k["cpu_weights_mb"] == 0.0

                ngl_q3_post_32k, can_run_q3_32k, _, audit_q3_32k = LayerBudgeter.calculate_layer_offload(
                    model_path="dummy_gemma4_q3km.gguf",
                    target_context=32768,
                    total_vram_mb=8151,
                    current_used_vram_mb=522.0,
                    available_ram_mb=16384.0,
                    cache_type="q8_0_q4_0",
                    is_arbiter_managed=True,
                )
                assert can_run_q3_32k is True
                assert ngl_q3_post_32k in (48, 99)
                assert audit_q3_32k["cpu_weights_mb"] == 0.0

class TestProfile16GB:
    """4. 16 ГБ VRAM / 32 ГБ RAM: тяжелые модели и 32B с фоном."""

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

            # Чистый старт (фон 700 МБ) -> ngl == 42 или 43
            ngl_clean, _, _, audit_clean = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_32b.gguf",
                target_context=11136,
                total_vram_mb=16384,
                current_used_vram_mb=700.0,
                available_ram_mb=32768.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )
            assert ngl_clean in (42, 43)

            # С фоном 1300 МБ -> ngl снижается до 39..40
            ngl_bg, _, _, audit_bg = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_32b.gguf",
                target_context=11136,
                total_vram_mb=16384,
                current_used_vram_mb=1300.0,
                available_ram_mb=32768.0,
                cache_type="q8_0",
                is_arbiter_managed=True,
            )
            assert ngl_bg in (39, 40)
            # Запас под WDDM
            used_gpu = 1300 + 3900 + (ngl_bg * 243.75) + audit_bg["kv_cache_mb"] + audit_bg["compute_buffer_mb"]
            assert (16384 - used_gpu) >= 170


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
        client._current_running_ngl = None
        client._current_total_layers = None
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


class TestLlamaServerIncidentAuditFixes:
    """6. Тесты на архитектурные исправления инцидента OOM llama-server после Forge."""

    def test_process_manager_extracts_startup_error_and_oom_flag(self, tmp_path, monkeypatch):
        """Парсинг LMSTUDIO_STARTUP_ERROR и выставление is_oom_failure=True в ProcessManager."""
        fake_logs_dir = tmp_path / "logs"
        fake_logs_dir.mkdir(parents=True, exist_ok=True)
        fake_log = fake_logs_dir / "llama_server.log"
        fake_log.write_text(
            "0.15.124.101 E ggml_backend_cuda_buffer_type_alloc_buffer: allocating 136.01 MiB on device 0: cudaMalloc failed: out of memory\n"
            'LMSTUDIO_STARTUP_ERROR:{"schemaVersion":1,"code":"model_load_failed","message":"Failed to initialize the context: failed to allocate compute pp buffers"}\n',
            encoding="utf-8"
        )
        monkeypatch.setattr("services.process_manager.LOGS_DIR", fake_logs_dir)

        pm = ProcessManager()
        msg, is_oom = pm._extract_llama_startup_error()
        assert is_oom is True
        assert pm.is_oom_failure is True
        assert "Failed to initialize the context" in msg
        assert "CUDA Out of Memory" in msg
        assert pm.last_error == msg

    def test_ensure_model_loaded_oom_step_down(self, monkeypatch, tmp_path):
        """Однократный безопасный OOM step-down (NGL -> NGL - 4) в ensure_model_loaded без удержания _process_lock."""
        from services.lm_studio import LMStudioClient
        from services.server_config import ServerConfigResolver

        dummy_model = tmp_path / "test_model.gguf"
        dummy_model.write_text("gguf")
        monkeypatch.setattr(ServerConfigResolver, "resolve_model_path", lambda m: dummy_model)
        monkeypatch.setattr(LayerBudgeter, "parse_gguf_metadata", lambda p: {"total_layers": 48})

        client = LMStudioClient()
        mock_pm = MagicMock()
        mock_pm.has_headless_server.return_value = True

        call_records = []
        def mock_start(model_identifier, context_length, gpu_layers):
            call_records.append(gpu_layers)
            if len(call_records) == 1:
                mock_pm.is_oom_failure = True
                mock_pm._last_attempted_ngl = 43
                mock_pm.last_error = "CUDA Out of Memory"
                return False
            else:
                mock_pm.is_oom_failure = False
                return True

        mock_pm.start_llama_server_sync.side_effect = mock_start
        client.set_process_manager(mock_pm)
        client._is_model_unloaded = True

        ok = client.ensure_model_loaded(preferred_model="test_model", context_length=14336)
        assert ok is True
        assert len(call_records) == 2
        # Первый вызов с дефолтным/рассчитанным ngl, второй со step-down: 43 - 4 = 39
        assert call_records[1] == 39

    def test_vram_arbiter_owner_transition_and_fragmentation_margin(self):
        """Переход DIFFUSION -> NONE -> LLM в VRAMArbiter и временный fragmentation_margin_mb = 180 в первые 8с после Forge."""
        import time
        from services.vram_arbiter import VRAMArbiter, GPUOwner

        arb = VRAMArbiter.get_instance()
        arb._current_owner = GPUOwner.DIFFUSION

        # Вызов release_diffusion_for_llm сбрасывает владельца в NONE (а не LLM) и ставит timestamp
        arb.release_diffusion_for_llm(force=True)
        assert arb._current_owner == GPUOwner.NONE
        assert time.monotonic() - arb._last_diffusion_release_ts < 2.0

        # В первые 8 секунд после Forge:
        # Проверяем, что calculate_layer_offload с fragmentation_margin_mb=180 корректно вычитает маржу
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=GEMMA4_12B_META), \
             patch.object(LayerBudgeter, "get_current_used_vram_mb", return_value=684.0):
            ngl_normal, _, _, audit_normal = LayerBudgeter.calculate_layer_offload(
                model_path="dummy.gguf",
                target_context=14336,
                total_vram_mb=8192,
                available_ram_mb=16384,
                is_arbiter_managed=True,
                fragmentation_margin_mb=0
            )
            ngl_frag, _, _, audit_frag = LayerBudgeter.calculate_layer_offload(
                model_path="dummy.gguf",
                target_context=14336,
                total_vram_mb=8192,
                available_ram_mb=16384,
                is_arbiter_managed=True,
                fragmentation_margin_mb=180
            )
            # При fragmentation_margin_mb=180 доступная память Usable VRAM меньше ровно на 180 МБ
            assert audit_normal["usable_vram_mb"] - audit_frag["usable_vram_mb"] == 180
            assert audit_frag["fragmentation_margin_mb"] == 180
            assert ngl_frag <= ngl_normal

        # При вызове prepare_for_task("llm") происходит переход NONE -> LLM
        arb.prepare_for_task("llm")
        assert arb._current_owner == GPUOwner.LLM

    def test_vram_arbiter_dwell_window_stabilization(self, monkeypatch):
        """Проверка, что _wait_for_vram_stabilization не выходит на шаге 0, а выдерживает dwell_s."""
        import time
        from services.vram_arbiter import VRAMArbiter

        arb = VRAMArbiter.get_instance()
        readings = [684.0, 684.0, 684.0, 684.0, 684.0]
        idx = 0
        def mock_used():
            nonlocal idx
            val = readings[min(idx, len(readings) - 1)]
            idx += 1
            return val

        monkeypatch.setattr(LayerBudgeter, "get_current_used_vram_mb", mock_used)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        fake_time = 100.0
        def mock_monotonic():
            nonlocal fake_time
            t = fake_time
            fake_time += 0.2
            return t

        monkeypatch.setattr(time, "monotonic", mock_monotonic)
        arb._wait_for_vram_stabilization(timeout_s=2.5, step_s=0.1, dwell_s=0.6)
        assert idx >= 3

    def test_evicted_for_forge_ngl_normalization_and_model_binding(self):
        """Проверка нормализации min(ngl, total_layers) и привязки к модели при выгрузке в пользу Forge."""
        pm = ProcessManager()
        mock_lms = MagicMock()
        mock_lms._last_loaded_model_id = "gemma-4-12b-it-uncensored-Q4_K_S.gguf"
        pm.lm_client = mock_lms

        pm._llama_process = MagicMock()
        pm._current_running_ngl = 99
        pm._current_total_layers = 48

        # 1. Выгрузка в пользу Forge -> сохраняется нормализованный NGL = 48 (а не 99)
        with patch("subprocess.run"):
            pm._kill_llama_server_instance(for_forge_eviction=True)

        assert pm._evicted_for_forge_ngl == 48
        assert pm._evicted_for_forge_model == "gemma-4-12b-it-uncensored-Q4_K_S.gguf"

    def test_evicted_for_forge_ngl_reset_on_model_switch(self):
        """При смене модели после Forge сохраненный NGL старой модели сбрасывается и не передается новой."""
        pm = ProcessManager()
        pm._evicted_for_forge_ngl = 48
        pm._evicted_for_forge_model = "gemma-4-12b-it-uncensored-Q4_K_S.gguf"

        captured_ngl = None
        def mock_calc(*args, **kwargs):
            nonlocal captured_ngl
            captured_ngl = kwargs.get("current_running_ngl")
            return (48, True, "OK", {"total_layers": 48})

        with patch("services.process_manager.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")), \
             patch("services.process_manager.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/qwen2.5-7b.gguf")), \
             patch("services.process_manager.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.process_manager.ServerConfigResolver.resolve_cuda_dir", return_value=None), \
             patch("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", return_value=(True, 150)), \
             patch("services.layer_budgeter.LayerBudgeter.get_current_used_vram_mb", return_value=590.5), \
             patch("services.layer_budgeter.LayerBudgeter.calculate_layer_offload", side_effect=mock_calc), \
             patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
             patch("subprocess.Popen", side_effect=RuntimeError("stop_after_budget")), \
             patch("pathlib.Path.exists", return_value=True):

            try:
                pm.start_llama_server_sync(model_identifier="qwen2.5-7b.gguf", context_length=8192)
            except RuntimeError:
                pass

        # При смене модели gemma -> qwen2.5 сохраненный NGL сброшен в None
        assert captured_ngl is None
        assert pm._evicted_for_forge_ngl is None
        assert pm._evicted_for_forge_model is None

    def test_layer_budgeter_hysteresis_normalizes_99_to_total_layers(self):
        """LayerBudgeter при current_running_ngl=99 нормализует дельту к total_layers."""
        meta = {
            "total_layers": 48,
            "model_size_mb": 6698.65,
            "architecture": "gemma4",
            "is_moe": False,
            "context_length": 131072,
            "sliding_window": 1024,
            "vocab_size": 262144,
        }
        with patch.object(LayerBudgeter, "detect_gpu_topology", return_value=(True, 150)), \
             patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=meta):

            # При used_vram = 150 MB на CTX=4096 Usable VRAM достаточна под вес всех блоков (5358.92 MB) -> raw_ngl = 99
            ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
                model_path="dummy_gemma4.gguf",
                target_context=4096,
                total_vram_mb=8151,
                current_used_vram_mb=150.0,
                current_running_ngl=99,
                is_arbiter_managed=True,
                fragmentation_margin_mb=0,
            )
            assert ngl == 99
            assert audit["final_ngl"] == 99
            assert audit["available_for_blocks_mb"] >= audit["blocks_weight_mb"]
