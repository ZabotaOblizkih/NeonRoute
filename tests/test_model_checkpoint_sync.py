"""
Tests for model checkpoint synchronization and stem normalization.
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from PyQt6.QtWidgets import QApplication

from ui.models_manager_dialog import ModelsManagerDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def mock_forge_client():
    client = MagicMock()
    client.get_models.return_value = []
    client.get_installed_checkpoints.return_value = []
    client.get_upscalers.return_value = ["4x-UltraSharp"]
    return client


def test_normalize_checkpoint_stem():
    """Проверяет корректность очистки имени чекпоинта от суффиксов квантования."""
    assert ModelsManagerDialog._normalize_checkpoint_stem("model_v10_fp8e4.safetensors") == "model_v10"
    assert ModelsManagerDialog._normalize_checkpoint_stem("model_v10_fp8.safetensors") == "model_v10"
    assert ModelsManagerDialog._normalize_checkpoint_stem("model_v10_e4m3fn.safetensors") == "model_v10"
    assert ModelsManagerDialog._normalize_checkpoint_stem("model_v10_fp16.safetensors") == "model_v10"
    assert ModelsManagerDialog._normalize_checkpoint_stem("model_v10_pruned.safetensors") == "model_v10"
    assert ModelsManagerDialog._normalize_checkpoint_stem("") == ""
    assert ModelsManagerDialog._normalize_checkpoint_stem(None) == ""


def test_combo_selection_fallback_when_target_not_in_available(qapp, mock_forge_client):
    """
    Проверяет, что при смене карточки в комбобоксе выставляется целевой чекпоинт,
    даже если исходного файла нет в списке доступных.
    """
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dlg = ModelsManagerDialog(forge_client=mock_forge_client)

    # Задаем исходный набор доступных чекпоинтов в комбобоксе
    dlg.combo_checkpoint.clear()
    dlg.combo_checkpoint.addItems(["initial_checkpoint_a.safetensors", "initial_checkpoint_b.safetensors"])
    dlg.combo_checkpoint.setCurrentIndex(0)
    assert dlg.combo_checkpoint.currentText() == "initial_checkpoint_a.safetensors"

    # Добавляем модель, чекпоинта которой физически нет в комбобоксе
    test_missing_ckpt = "non_existent_custom_model_v1.safetensors"
    dlg.row_widgets.append({
        "id": "custom_test_model",
        "name": "Custom Test Model",
        "checkpoint": test_missing_ckpt,
        "architecture": "sdxl",
        "routing_description": "Test model",
        "llm_guidance": "",
        "pipeline": {}
    })

    # Загружаем модель в форму
    target_row_idx = len(dlg.row_widgets) - 1
    dlg._load_model_to_form(target_row_idx)

    # Проверяем, что комбобокс не залип на 'initial_checkpoint_a.safetensors',
    # а переключился на целевой чекпоинт
    assert dlg.combo_checkpoint.currentText() == test_missing_ckpt
    assert dlg.combo_checkpoint.currentIndex() >= 0
    assert dlg.combo_checkpoint.itemText(dlg.combo_checkpoint.currentIndex()) == test_missing_ckpt


def test_combo_selection_fallback_normalized_stem(qapp, mock_forge_client):
    """
    Проверяет фоллбэк сопоставления чекпоинта по нормализованному стэму (например, FP8-версии).
    """
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dlg = ModelsManagerDialog(forge_client=mock_forge_client)

    # В комбобоксе доступна FP8-версия
    dlg.combo_checkpoint.clear()
    dlg.combo_checkpoint.addItems(["other_model.safetensors", "dream_model_v1_fp8e4.safetensors"])
    dlg.combo_checkpoint.setCurrentIndex(0)

    # В модели прописан базовый чекпоинт без fp8 суффикса
    dlg.row_widgets.append({
        "id": "dream_model",
        "name": "Dream Model",
        "checkpoint": "dream_model_v1.safetensors",
        "architecture": "sdxl",
        "routing_description": "Dream model",
        "pipeline": {}
    })

    target_row_idx = len(dlg.row_widgets) - 1
    dlg._load_model_to_form(target_row_idx)

    # Должен найти эквивалентную FP8 версию по нормализованному стэму
    assert dlg.combo_checkpoint.currentText() == "dream_model_v1_fp8e4.safetensors"
    assert dlg.combo_checkpoint.currentIndex() == 1


def test_auto_registration_deduplication(qapp, mock_forge_client):
    """
    Проверяет исключение дубликатов профилей при автосканировании,
    если на диске обнаружен чекпоинт с аналогичным нормализованным именем.
    """
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dlg = ModelsManagerDialog(forge_client=mock_forge_client)

    dlg.row_widgets = [
        {
            "id": "auto_aamxlanimemix_v10",
            "name": "aamXLAnimeMix_v10",
            "checkpoint": "aamXLAnimeMix_v10.safetensors",
            "architecture": "sdxl",
            "routing_description": "User description",
            "pipeline": {"steps": 35}
        }
    ]

    initial_count = len(dlg.row_widgets)

    # Сканирование Forge возвращает FP8 версию того же чекпоинта
    raw_models = [{"title": "aamXLAnimeMix_v10_fp8e4.safetensors", "model_name": "aamXLAnimeMix_v10_fp8e4"}]
    installed_list = ["C:/models/aamXLAnimeMix_v10_fp8e4.safetensors"]

    dlg._scan_silent = True
    dlg._on_forge_scan_completed(raw_models, installed_list, ["4x-UltraSharp"])

    # Не должно быть создано дубликата карточки
    assert len(dlg.row_widgets) == initial_count
    # Чекпоинт должен тихо обновиться на актуальный с диска
    assert dlg.row_widgets[0]["checkpoint"] == "aamXLAnimeMix_v10_fp8e4.safetensors"
    # Пользовательские настройки не должны быть сброшены
    assert dlg.row_widgets[0]["pipeline"]["steps"] == 35


def test_fp8_conversion_saves_to_json(qapp, mock_forge_client, tmp_path):
    """
    Проверяет, что после завершения FP8 конвертации вызывается сохранение на диск.
    """
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dlg = ModelsManagerDialog(forge_client=mock_forge_client)

    new_file = tmp_path / "test_model_v1_fp8e4.safetensors"
    new_file.write_text("dummy")

    dlg.row_widgets = [
        {
            "id": "test_model",
            "name": "Test Model",
            "checkpoint": "test_model_v1.safetensors",
            "architecture": "sdxl",
            "routing_description": "Test",
            "active": True,
            "pipeline": {}
        }
    ]
    dlg.current_selected_row = 0

    with patch.object(dlg, "_save_models_to_json") as mock_save, \
         patch("ui.models_manager_dialog.InfoBar"):
        dlg._on_fp8_finished(str(new_file))
        mock_save.assert_called_once()
