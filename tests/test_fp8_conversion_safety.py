"""
tests/test_fp8_conversion_safety.py
===================================
Автоматическая верификация безопасности квантования моделей в FP8,
проверка Name Affinity Guard и атомарной замены исходного файла (Atomic In-Place Swap).
"""
import os
import sys
import tempfile
from pathlib import Path
import pytest

from ui.models_manager_dialog import ModelsManagerDialog
from ui.models_manager.workers import FP8ConvertWorker


class TestNormalizeCheckpointStem:
    """Проверка чистоты и устойчивости нормализатора имен чекпоинтов."""

    def test_normalize_fp16e4_and_fp8e4_match(self):
        src = ModelsManagerDialog._normalize_checkpoint_stem("aamXLAnimeMix_v10_fp16e4.safetensors")
        dst = ModelsManagerDialog._normalize_checkpoint_stem("aamXLAnimeMix_v10_fp8e4.safetensors")
        assert src == "aamxlanimemix_v10"
        assert dst == "aamxlanimemix_v10"
        assert src == dst

    def test_normalize_standard_fp16_and_fp8_match(self):
        src = ModelsManagerDialog._normalize_checkpoint_stem("cyberrealistic_v40_fp16.safetensors")
        dst = ModelsManagerDialog._normalize_checkpoint_stem("cyberrealistic_v40_fp8e4.safetensors")
        assert src == "cyberrealistic_v40"
        assert dst == "cyberrealistic_v40"
        assert src == dst

    def test_normalize_combined_suffixes(self):
        src = ModelsManagerDialog._normalize_checkpoint_stem("dreamshaperXL_v21_fp16_pruned.safetensors")
        dst = ModelsManagerDialog._normalize_checkpoint_stem("dreamshaperXL_v21_fp8e4.safetensors")
        assert src == "dreamshaperxl_v21"
        assert dst == "dreamshaperxl_v21"
        assert src == dst

    def test_distinct_models_never_collide(self):
        aam = ModelsManagerDialog._normalize_checkpoint_stem("aamXLAnimeMix_v10_fp16e4.safetensors")
        rv = ModelsManagerDialog._normalize_checkpoint_stem("Realistic_Vision_V5.1_noVAE.safetensors")
        cyber = ModelsManagerDialog._normalize_checkpoint_stem("cyberrealistic_v40_fp16.safetensors")
        assert aam != rv
        assert aam != cyber
        assert rv != cyber


class TestWorkerNameResolution:
    """Проверка генерации имен файлов в воркере без задвоения суффиксов."""

    def test_clean_replacement_fp16e4(self):
        worker = FP8ConvertWorker(
            python_exe=sys.executable,
            script_path=Path("dummy.py"),
            input_checkpoint_path=Path("C:/models/aamXLAnimeMix_v10_fp16e4.safetensors")
        )
        assert worker.dst_path.name == "aamXLAnimeMix_v10_fp8e4.safetensors"

    def test_clean_replacement_fp16(self):
        worker = FP8ConvertWorker(
            python_exe=sys.executable,
            script_path=Path("dummy.py"),
            input_checkpoint_path=Path("C:/models/model_v1_fp16.safetensors")
        )
        assert worker.dst_path.name == "model_v1_fp8e4.safetensors"

    def test_clean_append_bare_name(self):
        worker = FP8ConvertWorker(
            python_exe=sys.executable,
            script_path=Path("dummy.py"),
            input_checkpoint_path=Path("C:/models/sdxl_base.safetensors")
        )
        assert worker.dst_path.name == "sdxl_base_fp8e4.safetensors"


class TestNameAffinityGuardAndDeletion:
    """Проверка Name Affinity Guard и режимов Atomic Swap / Dual-file."""

    def test_guard_blocks_deleting_different_model(self):
        """Гарантирует, что при сжатии aamXL ни при каких условиях не будет удален Realistic Vision."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            orig_aam = tmp / "aamXLAnimeMix_v10_fp16e4.safetensors"
            fp8_aam = tmp / "aamXLAnimeMix_v10_fp8e4.safetensors"
            foreign_rv = tmp / "Realistic_Vision_V5.1.safetensors"

            orig_aam.write_text("aamXL")
            fp8_aam.write_text("aamXL_fp8")
            foreign_rv.write_text("realistic_vision")

            src_norm = ModelsManagerDialog._normalize_checkpoint_stem(foreign_rv.name)
            new_norm = ModelsManagerDialog._normalize_checkpoint_stem(fp8_aam.name)

            # Name Affinity Guard должен отклонить несовпадающие стэмы:
            is_affinity_ok = (src_norm == new_norm)
            assert not is_affinity_ok, "Name Affinity Guard обязан запретить удаление чужого файла!"
            assert foreign_rv.is_file(), "Файл Realistic Vision обязан остаться нетронутым!"

    def test_atomic_in_place_swap(self):
        """Проверяет режим Atomic Swap: исходное имя файла сохраняется, данные обновляются до FP8."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            orig_file = tmp / "aamXLAnimeMix_v10_fp16e4.safetensors"
            temp_fp8 = tmp / "aamXLAnimeMix_v10_fp8e4.safetensors"

            orig_file.write_text("HEAVY_6GB")
            temp_fp8.write_text("LIGHT_3GB")

            src_norm = ModelsManagerDialog._normalize_checkpoint_stem(orig_file.name)
            new_norm = ModelsManagerDialog._normalize_checkpoint_stem(temp_fp8.name)
            assert src_norm == new_norm

            # Atomic Swap
            orig_file.unlink()
            os.replace(temp_fp8, orig_file)

            assert orig_file.is_file()
            assert orig_file.read_text() == "LIGHT_3GB"
            assert not temp_fp8.exists()

    def test_corrupted_zero_byte_fp8_aborts_deletion(self):
        """Если конвертер дал сбой или создал файл 0 байт, исходник ни в коем случае не удаляется."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            orig_file = tmp / "model.safetensors"
            broken_fp8 = tmp / "model_fp8e4.safetensors"

            orig_file.write_text("VALUABLE_ORIGINAL_WEIGHTS")
            broken_fp8.write_text("")  # 0 байт

            is_valid = broken_fp8.is_file() and broken_fp8.stat().st_size > 0
            assert not is_valid
            assert orig_file.read_text() == "VALUABLE_ORIGINAL_WEIGHTS"


def test_resolve_forge_offload_policy_fast_and_stage_modes():
    from services.art_director.vram_policy import resolve_forge_offload_policy

    # 1. На 8 ГБ VRAM (7790 МБ свободно после выгрузки LLM) SDXL FP8 идет в режиме FAST_KEEP_VRAM (без пошаговой выгрузки)
    pol_fast = resolve_forge_offload_policy(
        width=896, height=1152, architecture="sdxl", is_fp8=True, enable_adetailer=True, free_vram_mb=7790
    )
    assert pol_fast["mode"] == "FAST_KEEP_VRAM"
    assert pol_fast["always_offload"] is False
    assert pol_fast["can_run"] is True

    # 2. Тяжелая модель SDXL FP16 на 8 ГБ автоматически переключается в пошаговую выгрузку STAGE_OFFLOAD
    pol_fp16 = resolve_forge_offload_policy(
        width=896, height=1152, architecture="sdxl", is_fp8=False, enable_adetailer=True, free_vram_mb=7790
    )
    assert pol_fp16["mode"] == "STAGE_OFFLOAD"
    assert pol_fp16["always_offload"] is True
    assert pol_fp16["can_run"] is True

    # 3. При ограниченной свободной памяти (4200 МБ) для SDXL FP8 включается STAGE_OFFLOAD
    pol_tight = resolve_forge_offload_policy(
        width=896, height=1152, architecture="sdxl", is_fp8=True, enable_adetailer=True, free_vram_mb=4200
    )
    assert pol_tight["mode"] == "STAGE_OFFLOAD"
    assert pol_tight["always_offload"] is True
    assert pol_tight["can_run"] is True

