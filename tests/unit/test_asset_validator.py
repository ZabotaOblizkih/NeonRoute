import os
import sys
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from services.asset_validator import AssetValidator, REQUIRED_CHECKPOINTS, REQUIRED_ADETAILER_MODELS


def test_asset_validator_standard_forge_structure(tmp_path, monkeypatch):
    """
    Проверяет динамическое разрешение стандартной чистой структуры Forge:
    <forge_root>/webui/
    <forge_root>/models/Stable-diffusion/
    <forge_root>/models/adetailer/
    """
    forge_root = tmp_path / "MyForge"
    webui_dir = forge_root / "webui"
    sd_dir = forge_root / "models" / "Stable-diffusion"
    ad_dir = forge_root / "models" / "adetailer"

    webui_dir.mkdir(parents=True)
    sd_dir.mkdir(parents=True)
    ad_dir.mkdir(parents=True)

    # Маркер webui
    (webui_dir / "webui.py").write_text("# webui", encoding="utf-8")
    (forge_root / "forge_launcher.bat").write_text("@echo off", encoding="utf-8")

    # Чекпоинты
    (sd_dir / "realisticVisionV60_v60B1.safetensors").write_text("dummy", encoding="utf-8")
    (sd_dir / "cyberrealistic_classic_v3.safetensors").write_text("dummy", encoding="utf-8")
    (sd_dir / "meinahentai_v5.safetensors").write_text("dummy", encoding="utf-8")
    (sd_dir / "dreamshaper_8.safetensors").write_text("dummy", encoding="utf-8")

    # ADetailer
    (ad_dir / "face_yolov8n.pt").write_text("weights", encoding="utf-8")
    (ad_dir / "hand_yolov8n.pt").write_text("weights", encoding="utf-8")

    monkeypatch.setattr("services.asset_validator.FORGE_ROOT_DIR", forge_root)
    monkeypatch.setattr("services.asset_validator.FORGE_BASE_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_SD_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_ADETAILER_DIR", None)

    report = AssetValidator.validate_all_assets()

    assert report["forge_installed"] is True
    assert "resolved_paths" in report
    paths = report["resolved_paths"]
    assert paths["forge_root"] == forge_root.resolve()
    assert paths["webui_dir"] == webui_dir.resolve()
    assert paths["sd_models_dir"] == sd_dir.resolve()

    # Все ключевые чекпоинты найдены
    for title, _ in REQUIRED_CHECKPOINTS:
        assert report["checkpoints_found"][title] is True
    assert len(report["checkpoints_missing"]) == 0
    assert report["all_critical_present"] is True

    # ADetailer найден
    assert report["adetailer_found"]["face_yolov8n.pt"] is True
    assert report["adetailer_found"]["hand_yolov8n.pt"] is True
    assert len(report["adetailer_missing"]) == 0

    # Проверка методов поиска ADetailer
    face_path = AssetValidator.find_adetailer_model("face_yolov8n.pt")
    assert face_path == (ad_dir / "face_yolov8n.pt").resolve()
    assert AssetValidator.is_adetailer_available("face_yolov8n.pt") is True


def test_asset_validator_legacy_cu121_package_structure(tmp_path, monkeypatch):
    """
    Проверяет разрешение пакета со вложенным каталогом webui_forge_cu121_torch231/webui.
    """
    forge_root = tmp_path / "neyroGPD"
    webui_dir = forge_root / "webui_forge_cu121_torch231" / "webui"
    sd_dir = webui_dir / "models" / "Stable-diffusion"
    ad_dir = webui_dir / "models" / "adetailer"

    webui_dir.mkdir(parents=True)
    sd_dir.mkdir(parents=True)
    ad_dir.mkdir(parents=True)

    (webui_dir / "webui.py").write_text("# webui", encoding="utf-8")
    (forge_root / "run.bat").write_text("@echo off", encoding="utf-8")
    (sd_dir / "dreamshaper_8_99.safetensors").write_text("data", encoding="utf-8")

    monkeypatch.setattr("services.asset_validator.FORGE_ROOT_DIR", forge_root)
    monkeypatch.setattr("services.asset_validator.FORGE_BASE_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_SD_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_ADETAILER_DIR", None)

    report = AssetValidator.validate_all_assets()

    assert report["forge_installed"] is True
    assert report["resolved_paths"]["webui_dir"] == webui_dir.resolve()
    assert report["resolved_paths"]["sd_models_dir"] == sd_dir.resolve()
    assert report["checkpoints_found"]["DreamShaper 8"] is True
    assert report["checkpoints_found"]["Realistic Vision v6.0"] is False
    assert report["all_critical_present"] is False


def test_asset_validator_launcher_custom_arguments(tmp_path, monkeypatch):
    """
    Проверяет, что аргументы --ckpt-dir и --models-dir из forge_launcher.bat имеют наивысший приоритет.
    """
    forge_root = tmp_path / "ForgeWithArgs"
    webui_dir = forge_root / "webui"
    webui_dir.mkdir(parents=True)
    (webui_dir / "webui.py").write_text("# webui", encoding="utf-8")

    custom_models = tmp_path / "ExternalModels"
    custom_ckpt = custom_models / "CustomSD"
    custom_ad = custom_models / "adetailer"
    custom_ckpt.mkdir(parents=True)
    custom_ad.mkdir(parents=True)

    (custom_ckpt / "realisticvision_v60.safetensors").write_text("data", encoding="utf-8")
    (custom_ad / "face_yolov8n.pt").write_text("weights", encoding="utf-8")

    launcher_file = forge_root / "forge_launcher.bat"
    launcher_file.write_text(
        f'@echo off\ncall launch.py --ckpt-dir "{custom_ckpt}" --models-dir "{custom_models}"\n',
        encoding="utf-8"
    )

    monkeypatch.setattr("services.asset_validator.FORGE_ROOT_DIR", forge_root)
    monkeypatch.setattr("services.asset_validator.FORGE_BASE_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_SD_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_ADETAILER_DIR", None)

    report = AssetValidator.validate_all_assets()

    assert report["forge_installed"] is True
    assert report["resolved_paths"]["sd_models_dir"] == custom_ckpt.resolve()
    assert report["checkpoints_found"]["Realistic Vision v6.0"] is True

    # ADetailer найден через --models-dir / adetailer
    found_ad = AssetValidator.find_adetailer_model("face_yolov8n.pt")
    assert found_ad == (custom_ad / "face_yolov8n.pt").resolve()


def test_asset_validator_junction_resolution(tmp_path, monkeypatch):
    """
    Проверяет корректность вызова .resolve() для раскрытия симлинков и NTFS Junctions.
    """
    real_target = tmp_path / "RealTarget"
    real_target.mkdir()
    (real_target / "webui.py").write_text("# webui", encoding="utf-8")
    (real_target / "forge_launcher.bat").write_text("@echo off", encoding="utf-8")

    link_dir = tmp_path / "JunctionLink"
    try:
        os.symlink(str(real_target), str(link_dir), target_is_directory=True)
        test_dir = link_dir
    except (OSError, NotImplementedError):
        # Если нет привилегий Windows Developer Mode для создания symlink/junction,
        # проверяем, что .resolve() вызывается и возвращает канонический путь
        test_dir = real_target

    monkeypatch.setattr("services.asset_validator.FORGE_ROOT_DIR", test_dir)
    monkeypatch.setattr("services.asset_validator.FORGE_BASE_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_SD_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_ADETAILER_DIR", None)

    report = AssetValidator.validate_all_assets()
    assert report["forge_installed"] is True
    # Путь обязательно нормализован
    assert report["resolved_paths"]["forge_root"] == test_dir.resolve()


def test_asset_validator_missing_paths_resilience(tmp_path, monkeypatch, caplog):
    """
    Проверяет, что при полном отсутствии каталогов Forge и моделей валидатор
    не падает с исключениями, возвращает forge_installed=False и корректно логирует аудит.
    """
    non_existent = tmp_path / "NonExistentForge"
    monkeypatch.setattr("services.asset_validator.FORGE_ROOT_DIR", non_existent)
    monkeypatch.setattr("services.asset_validator.FORGE_BASE_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_SD_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_ADETAILER_DIR", None)
    monkeypatch.setattr("core.forge_locator.find_forge_root", lambda *a, **kw: None)

    with caplog.at_level(logging.WARNING, logger="NeonRoute.AssetValidator"):
        report = AssetValidator.validate_all_assets()
        assert report["forge_installed"] is False
        assert report["all_critical_present"] is False
        assert len(report["checkpoints_missing"]) == len(REQUIRED_CHECKPOINTS)
        assert len(report["adetailer_missing"]) == len(REQUIRED_ADETAILER_MODELS)

        AssetValidator.log_validation_summary()
        assert any("Директория Forge не обнаружена" in record.message for record in caplog.records)


def test_asset_validator_log_summary_with_missing_models(tmp_path, monkeypatch, caplog):
    """
    Проверяет информативное логирование summary при частично отсутствующих ассетах.
    """
    forge_root = tmp_path / "PartialForge"
    webui_dir = forge_root / "webui"
    sd_dir = forge_root / "models" / "Stable-diffusion"
    webui_dir.mkdir(parents=True)
    sd_dir.mkdir(parents=True)
    (webui_dir / "webui.py").write_text("# webui", encoding="utf-8")

    # Только один чекпоинт
    (sd_dir / "dreamshaper_8.safetensors").write_text("data", encoding="utf-8")

    monkeypatch.setattr("services.asset_validator.FORGE_ROOT_DIR", forge_root)
    monkeypatch.setattr("services.asset_validator.FORGE_BASE_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_SD_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_ADETAILER_DIR", None)

    with caplog.at_level(logging.INFO, logger="NeonRoute.AssetValidator"):
        AssetValidator.log_validation_summary()

        assert any("Pre-flight аудит ассетов" in record.message for record in caplog.records)
        assert any("Отсутствуют чекпоинты" in record.message for record in caplog.records)
        assert any("отсутствуют веса ADetailer" in record.message for record in caplog.records)


def test_asset_validator_ad_no_huggingface_branching(tmp_path, monkeypatch, caplog):
    """
    Проверяет ветвление логов при отсутствии весов ADetailer в зависимости от флага forge_ad_no_huggingface.
    """
    forge_root = tmp_path / "BranchForge"
    webui_dir = forge_root / "webui"
    webui_dir.mkdir(parents=True)
    (webui_dir / "webui.py").write_text("# webui", encoding="utf-8")

    monkeypatch.setattr("services.asset_validator.FORGE_ROOT_DIR", forge_root)
    monkeypatch.setattr("services.asset_validator.FORGE_BASE_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_SD_DIR", None)
    monkeypatch.setattr("services.asset_validator.MODELS_ADETAILER_DIR", None)

    # Ветка 1: флаг выключен (False, дефолт) -> информационный лог о попытке скачивания с HF
    monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: {"forge_ad_no_huggingface": False})
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="NeonRoute.AssetValidator"):
        AssetValidator.log_validation_summary()
        assert any("автоматически загрузить их с Hugging Face" in record.message for record in caplog.records)

    # Ветка 2: флаг включен (True) -> предупреждение о запрете автоскачивания
    monkeypatch.setattr("utils.config_manager.ConfigManager.load_config", lambda: {"forge_ad_no_huggingface": True})
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="NeonRoute.AssetValidator"):
        AssetValidator.log_validation_summary()
        assert any("запрещает автоскачивание" in record.message for record in caplog.records)
