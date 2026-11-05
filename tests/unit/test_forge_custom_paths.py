"""
Unit tests for custom model directories (--models-dir, --ckpt-dir, --lora-dir)
and NTFS symlink / junction resolution in SD WebUI Forge.
"""

from pathlib import Path
import pytest

from core.forge_locator import (
    parse_forge_launcher_args,
    resolve_forge_models_directory,
)


def test_parse_forge_launcher_args_quoted_and_spaces(tmp_path):
    """
    Создает тестовый батник с содержимым:
    set COMMANDLINE_ARGS=--models-dir "D:\\My Models\\ForgeModels" --ckpt-dir "E:\\Checkpoints\\SD" --port 7860
    Проверяет корректное извлечение путей models_dir и ckpt_dir с сохранением пробелов.
    """
    launcher_file = tmp_path / "forge_launcher.bat"
    content = (
        "@echo off\r\n"
        'set COMMANDLINE_ARGS=--models-dir "D:\\My Models\\ForgeModels" --ckpt-dir "E:\\Checkpoints\\SD" --port 7860\r\n'
        "call python launch.py %COMMANDLINE_ARGS%\r\n"
    )
    launcher_file.write_text(content, encoding="utf-8")

    parsed = parse_forge_launcher_args(launcher_file)
    assert "models_dir" in parsed
    assert "ckpt_dir" in parsed
    assert parsed["models_dir"] == Path(r"D:\My Models\ForgeModels").resolve()
    assert parsed["ckpt_dir"] == Path(r"E:\Checkpoints\SD").resolve()


def test_parse_forge_launcher_args_missing_or_empty(tmp_path):
    """
    Проверяет возврат пустого словаря при отсутствии аргументов или
    несуществующем батнике без необработанных исключений.
    """
    # 1. Передан None
    assert parse_forge_launcher_args(None) == {}

    # 2. Несуществующий файл
    non_existent = tmp_path / "missing_launcher.bat"
    assert parse_forge_launcher_args(non_existent) == {}

    # 3. Пустой файл
    empty_file = tmp_path / "empty_launcher.bat"
    empty_file.write_text("", encoding="utf-8")
    assert parse_forge_launcher_args(empty_file) == {}

    # 4. Батник без аргументов моделей
    no_flags_file = tmp_path / "no_flags.bat"
    no_flags_file.write_text("python launch.py --port 7860 --listen", encoding="utf-8")
    assert parse_forge_launcher_args(no_flags_file) == {}


def test_resolve_forge_models_directory_prefers_custom_dir(tmp_path):
    """
    Создает структуру с кастомным каталогом моделей и верифицирует,
    что резолвер возвращает именно кастомный путь, а не дефолтный forge_root / models.
    """
    forge_root = tmp_path / "forge_install"
    default_sd = forge_root / "models" / "Stable-diffusion"
    default_sd.mkdir(parents=True, exist_ok=True)

    custom_models = tmp_path / "external_models"
    custom_models.mkdir(parents=True, exist_ok=True)

    custom_ckpt = tmp_path / "dedicated_ckpts"
    custom_ckpt.mkdir(parents=True, exist_ok=True)

    # Сценарий A: Приоритет 1 - флаг --ckpt-dir
    launcher_ckpt = forge_root / "launcher_ckpt.bat"
    launcher_ckpt.write_text(
        f'set COMMANDLINE_ARGS=--models-dir "{custom_models}" --ckpt-dir "{custom_ckpt}"',
        encoding="utf-8"
    )
    res_ckpt = resolve_forge_models_directory(forge_root, launcher_ckpt)
    assert res_ckpt == custom_ckpt.resolve()

    # Сценарий B: Приоритет 2 - флаг --models-dir при отсутствии --ckpt-dir
    launcher_models = forge_root / "launcher_models.bat"
    launcher_models.write_text(
        f'set COMMANDLINE_ARGS=--models-dir "{custom_models}"',
        encoding="utf-8"
    )
    res_models = resolve_forge_models_directory(forge_root, launcher_models)
    assert res_models == (custom_models / "Stable-diffusion").resolve()

    # Сценарий C: Дефолтный путь, если батник не содержит кастомных аргументов
    launcher_clean = forge_root / "launcher_clean.bat"
    launcher_clean.write_text("python launch.py --port 7860", encoding="utf-8")
    res_default = resolve_forge_models_directory(forge_root, launcher_clean)
    assert res_default == default_sd.resolve()


def test_symlink_and_junction_resolution(tmp_path):
    """
    Проверяет, что вызов .resolve() корректно восстанавливает целевую директорию
    и не ломает поиск смежной папки Lora.
    """
    real_storage = tmp_path / "real_storage" / "models"
    real_sd = real_storage / "Stable-diffusion"
    real_lora = real_storage / "Lora"
    real_sd.mkdir(parents=True, exist_ok=True)
    real_lora.mkdir(parents=True, exist_ok=True)

    symlink_point = tmp_path / "symlink_point_sd"
    try:
        symlink_point.symlink_to(real_sd, target_is_directory=True)
        has_symlink = True
    except OSError:
        has_symlink = False

    if has_symlink:
        # Проверяем, что .resolve() раскрывает симлинк к реальной директории
        resolved_sd = symlink_point.resolve()
        assert resolved_sd == real_sd.resolve()

        # Проверяем расчет соседней папки Lora через .resolve().parent
        base_dir = symlink_point.resolve().parent
        lora_dir = (base_dir / "Lora").resolve()
        assert lora_dir == real_lora.resolve()
        assert lora_dir.is_dir()
    else:
        # Резервный тест резолвинга путей при ограничении привилегий Windows
        base_dir = real_sd.resolve().parent
        lora_dir = (base_dir / "Lora").resolve()
        assert lora_dir == real_lora.resolve()
        assert lora_dir.is_dir()
