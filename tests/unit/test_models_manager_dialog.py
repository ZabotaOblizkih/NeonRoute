"""
tests/unit/test_models_manager_dialog.py
========================================
Характеризационный тестовый сьют для ModelsManagerDialog:
- Инициализация и построение таблицы
- Выбор пресетов (SDXL Turbo, SDXL Classic, SD 1.5 Photoreal)
- Двусторонняя синхронизация слайдеров и счетчиков (SpinBox <-> Slider)
- Data Binding формы (_load_model_to_form, _save_form_to_model, эксклюзивность is_default)
- CRUD-операции таблицы моделей (добавление, удаление)
- Автонастройка параметров через Civitai (_on_civitai_params_ready)
- Сканирование Forge и авторегистрация новых чекпоинтов (_on_forge_scan_completed)
- Семантический роутинг и проверка бейджа (_run_test_match, _on_test_match_success)
- Сохранение конфигурации (_save_all, _save_models_to_json)
- Безопасная остановка воркеров (_stop_all_workers, reject, closeEvent)
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from services.model_router import ModelRouter
from services.art_director_config import save_art_director_models, load_art_director_models
from ui.models_manager_dialog import ModelsManagerDialog


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """
    Изолированное хранилище: перенаправляет ART_DIRECTOR_MODELS_PATH и ModelRouter
    во временный каталог теста tmp_path.
    """
    tmp_json_path = tmp_path / "art_director_models.json"
    ModelRouter._instance = None

    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", tmp_json_path)

    import ui.models_manager_dialog as mmd
    if hasattr(mmd, "ART_DIRECTOR_MODELS_PATH"):
        monkeypatch.setattr(mmd, "ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr(mmd, "setTheme", lambda *a, **kw: None)

    initial_models = [
        {
            "id": "model_sdxl_base",
            "name": "SDXL Base Anime",
            "architecture": "sdxl",
            "checkpoint": "sdxl_anime_v1.safetensors",
            "routing_description": "Аниме стилистика SDXL",
            "llm_guidance": "anime, masterpiece",
            "negative_prompt": "worst quality, blurry",
            "is_default": True,
            "style_triggers": ["anime", "manga"],
            "active": True,
            "pipeline": {
                "architecture": "sdxl",
                "sampler": "LCM",
                "scheduler": "Karras",
                "steps": 10,
                "cfg_scale": 1.3,
                "clip_skip": 2,
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
                "enable_hr": False,
                "enable_adetailer": True,
                "negative_prompt": "worst quality, blurry"
            }
        },
        {
            "id": "model_sd15_photo",
            "name": "SD 1.5 Realistic",
            "architecture": "sd15",
            "checkpoint": "realisticVision_v51.safetensors",
            "routing_description": "Фотореализм SD 1.5",
            "llm_guidance": "photorealistic, raw photo",
            "negative_prompt": "bad anatomy, doll",
            "is_default": False,
            "style_triggers": ["photo", "realism"],
            "active": False,
            "pipeline": {
                "architecture": "sd15",
                "sampler": "DPM++ 2M Karras",
                "scheduler": "Karras",
                "steps": 25,
                "cfg_scale": 6.0,
                "clip_skip": 1,
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.35,
                "enable_hr": True,
                "enable_adetailer": False,
                "negative_prompt": "bad anatomy, doll"
            }
        }
    ]
    save_art_director_models(initial_models)

    yield tmp_json_path

    ModelRouter._instance = None


def test_models_manager_init_and_table_population(qapp, isolated_storage):
    """Проверяет создание диалога, заполнение таблицы и загрузку первой строки."""
    mock_forge = MagicMock()
    mock_forge.get_models.return_value = []
    mock_forge.get_installed_checkpoints.return_value = []
    mock_forge.get_upscalers.return_value = []

    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog(forge_client=mock_forge)

    assert dialog.table.rowCount() == 2
    assert dialog.current_selected_row == 0
    assert dialog.input_name.text() == "SDXL Base Anime"
    assert dialog.combo_arch.currentText() == "SDXL / Pony"
    assert dialog.chk_is_default.isChecked() is True
    assert dialog.spin_steps.value() == 10
    assert abs(dialog.spin_cfg.value() - 1.3) < 0.05
    assert dialog.spin_clip_skip.value() == 2

    # Проверка виджетов в ячейках таблицы
    row0_chk = dialog.row_widgets[0]["chk"]
    assert row0_chk.isChecked() is True

    row1_chk = dialog.row_widgets[1]["chk"]
    assert row1_chk.isChecked() is False

    dialog.reject()


def test_models_manager_preset_selection(qapp, isolated_storage):
    """Проверяет выбор пресетов и их корректное применение к контролам формы."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    # Выбираем SDXL Turbo (индекс 1)
    dialog.combo_presets.setCurrentIndex(1)
    assert dialog.combo_arch.currentText() == "SDXL / Pony"
    assert dialog.combo_sampler.currentText() == "LCM"
    assert dialog.combo_scheduler.currentText() == "Karras"
    assert dialog.spin_steps.value() == 10
    assert abs(dialog.spin_cfg.value() - 1.3) < 0.05
    assert dialog.spin_clip_skip.value() == 2
    assert dialog.switch_enable_hr.isChecked() is False

    # Выбираем SDXL Classic (индекс 2)
    dialog.combo_presets.setCurrentIndex(2)
    assert dialog.combo_arch.currentText() == "SDXL / Pony"
    assert dialog.combo_sampler.currentText() == "DPM++ 2M"
    assert dialog.combo_scheduler.currentText() == "Karras"
    assert dialog.spin_steps.value() == 28
    assert abs(dialog.spin_cfg.value() - 5.0) < 0.05
    assert dialog.spin_clip_skip.value() == 2
    assert dialog.switch_enable_hr.isChecked() is False

    # Выбираем SD 1.5 Photoreal (индекс 3)
    dialog.combo_presets.setCurrentIndex(3)
    assert dialog.combo_arch.currentText() == "SD 1.5"
    assert dialog.combo_sampler.currentText() == "DPM++ SDE Karras"
    assert dialog.combo_scheduler.currentText() == "Karras"
    assert dialog.spin_steps.value() == 25
    assert abs(dialog.spin_cfg.value() - 5.5) < 0.05
    assert dialog.spin_clip_skip.value() == 1
    assert dialog.switch_enable_hr.isChecked() is True

    dialog.reject()


def test_models_manager_slider_spinbox_sync(qapp, isolated_storage):
    """Проверяет двустороннюю синхронизацию слайдеров и спинбоксов и точное позиционирование бегунка."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    dialog.show()

    # Steps: Slider -> Spin
    dialog.slider_steps.setValue(42)
    assert dialog.spin_steps.value() == 42
    expected_x = int((42 - 5) / (60 - 5) * dialog.slider_steps.grooveLength)
    assert dialog.slider_steps.handle.x() == expected_x

    # Steps: Spin -> Slider (даже при блокировке сигналов бегунок обязан сместиться)
    dialog.spin_steps.setValue(18)
    assert dialog.slider_steps.value() == 18
    expected_x = int((18 - 5) / (60 - 5) * dialog.slider_steps.grooveLength)
    assert dialog.slider_steps.handle.x() == expected_x

    # CFG: Slider -> Spin (деление на 10)
    dialog.slider_cfg.setValue(75)
    assert abs(dialog.spin_cfg.value() - 7.5) < 0.05
    expected_x = int((75 - 10) / (150 - 10) * dialog.slider_cfg.grooveLength)
    assert dialog.slider_cfg.handle.x() == expected_x

    # CFG: Spin -> Slider (умножение на 10, воспроизведение 7.0 со скриншота)
    dialog.spin_cfg.setValue(7.0)
    assert dialog.slider_cfg.value() == 70
    expected_x = int((70 - 10) / (150 - 10) * dialog.slider_cfg.grooveLength)
    assert dialog.slider_cfg.handle.x() == expected_x

    # Clip Skip: Spin -> Slider (значение 2 со скриншота пользователя)
    dialog.spin_clip_skip.setValue(2)
    assert dialog.slider_clip_skip.value() == 2
    expected_x = int((2 - 1) / (4 - 1) * dialog.slider_clip_skip.grooveLength)
    assert dialog.slider_clip_skip.handle.x() == expected_x

    # Hires Denoise: Spin -> Slider (значение 0.34 со скриншота пользователя)
    dialog.spin_hires_denoise.setValue(0.34)
    assert dialog.slider_hires_denoise.value() == 34
    expected_x = int((34 - 10) / (90 - 10) * dialog.slider_hires_denoise.grooveLength)
    assert dialog.slider_hires_denoise.handle.x() == expected_x

    dialog.reject()


def test_models_manager_form_data_binding(qapp, isolated_storage):
    """Проверяет сохранение данных формы в модель и переключение строк."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    # Модифицируем поля для строки 0
    dialog.input_name.setText("Updated SDXL Name")
    dialog.input_desc.setPlainText("Новое описание роутинга")
    dialog.input_guidance.setPlainText("prompt guidance tag1, tag2")
    dialog.input_negative_prompt.setPlainText("ugly, bad hands")
    dialog.spin_steps.setValue(33)
    dialog.spin_cfg.setValue(4.5)
    dialog.chk_is_default.setChecked(True)
    dialog.edit_style_triggers.setText("trigger1, trigger2")

    dialog._save_form_to_model(0)

    saved_row0 = dialog.row_widgets[0]
    assert saved_row0["name"] == "Updated SDXL Name"
    assert saved_row0["routing_description"] == "Новое описание роутинга"
    assert saved_row0["llm_guidance"] == "prompt guidance tag1, tag2"
    assert saved_row0["is_default"] is True
    assert saved_row0["style_triggers"] == ["trigger1", "trigger2"]
    assert saved_row0["pipeline"]["steps"] == 33
    assert abs(saved_row0["pipeline"]["cfg_scale"] - 4.5) < 0.05

    # Переключаемся на строку 1
    dialog.table.selectRow(1)
    dialog.current_selected_row = 1
    dialog._load_model_to_form(1)

    assert dialog.input_name.text() == "SD 1.5 Realistic"
    assert dialog.combo_arch.currentText() == "SD 1.5"
    assert dialog.spin_steps.value() == 25
    assert dialog.chk_is_default.isChecked() is False

    # Если делаем строку 1 default -> у строки 0 флаг сбрасывается
    dialog.chk_is_default.setChecked(True)
    dialog._save_form_to_model(1)
    assert dialog.row_widgets[1]["is_default"] is True
    assert dialog.row_widgets[0]["is_default"] is False

    dialog.reject()


def test_models_manager_table_crud(qapp, isolated_storage):
    """Проверяет добавление пустой модели и удаление строки."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    initial_count = dialog.table.rowCount()
    assert initial_count == 2

    # 1. Добавление новой строки
    dialog._add_empty_model_row()
    assert dialog.table.rowCount() == 3
    new_row_idx = dialog.current_selected_row
    assert new_row_idx == 2
    assert dialog.input_name.text() == "Новая модель"
    new_model_id = dialog.row_widgets[2]["id"]

    # 2. Удаление созданной строки с моком подтверждения
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog._remove_model_by_id(new_model_id)

    assert dialog.table.rowCount() == 2
    assert dialog.current_selected_row == 1

    dialog.reject()


def test_models_manager_civitai_params_ready_flow(qapp, isolated_storage):
    """Проверяет обработку полученных параметров из Civitai."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    dialog._autofill_target_row = 0
    civitai_response = {
        "status": "success",
        "sampler": "Euler a",
        "steps": 30,
        "cfg_scale": 7.0,
        "clip_skip": 2,
        "hires_upscaler": "4x-AnimeSharp",
        "hires_denoise": 0.40,
        "negative_prompt": "civitai negative sample"
    }

    with patch("ui.models_manager_dialog.InfoBar"):
        dialog._on_civitai_params_ready(civitai_response)

    assert dialog.combo_sampler.currentText() == "Euler a"
    assert dialog.spin_steps.value() == 30
    assert abs(dialog.spin_cfg.value() - 7.0) < 0.05
    assert dialog.spin_clip_skip.value() == 2
    assert dialog.combo_upscaler.currentText() == "4x-AnimeSharp"
    assert abs(dialog.spin_hires_denoise.value() - 0.40) < 0.01
    assert dialog.input_negative_prompt.toPlainText() == "civitai negative sample"

    # Синхронизация с целевой моделью строки
    assert dialog.row_widgets[0]["pipeline"]["sampler"] == "Euler a"
    assert dialog.row_widgets[0]["pipeline"]["steps"] == 30

    dialog.reject()


def test_models_manager_forge_scan_auto_registration(qapp, isolated_storage):
    """Проверяет автоматическую регистрацию новых чекпоинтов при сканировании Forge."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    assert dialog.table.rowCount() == 2

    raw_models = [
        {"filename": "C:/forge/models/Stable-diffusion/sdxl_anime_v1.safetensors"},  # Уже существует
        {"filename": "C:/forge/models/Stable-diffusion/new_cyberpunk_xl.safetensors"},  # Новый SDXL
    ]
    installed_list = [
        "C:/forge/models/Stable-diffusion/vintage_photo_sd15.safetensors"  # Новый SD 1.5
    ]
    upscalers = ["4x-UltraSharp", "ESRGAN_4x", "R-ESRGAN 4x+"]

    with patch.object(QMessageBox, "information"):
        dialog._on_forge_scan_completed(raw_models, installed_list, upscalers)

    # Должно добавиться 2 новые модели
    assert dialog.table.rowCount() == 4
    added_names = [r["name"] for r in dialog.row_widgets]
    assert "new_cyberpunk_xl" in added_names
    assert "vintage_photo_sd15" in added_names

    # Проверка, что апскейлеры добавлены в комбобокс
    for u in upscalers:
        assert dialog.combo_upscaler.findText(u) >= 0

    dialog.reject()


def test_models_manager_test_routing_flow(qapp, isolated_storage):
    """Проверяет тестирование семантического роутинга и бейдж статуса."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    # 1. Пустой ввод -> ошибка
    dialog.input_test_prompt.setText("")
    dialog._run_test_match()
    assert "Введите текст сцены" in dialog.result_badge.text()

    # 2. Успешный результат сопоставления
    dialog._on_test_match_success(
        winner_id="model_sdxl_base",
        score=0.88,
        reason="vector",
        profiles=[dialog.row_widgets[0]]
    )
    assert not dialog.result_badge.tag_lbl.isHidden()
    assert "FASTEMBED" in dialog.result_badge.tag_lbl.text()
    assert "SDXL Base Anime" in dialog.result_badge.text_lbl.text()

    # 3. Триггерный результат
    dialog._on_test_match_success(
        winner_id="model_sdxl_base",
        score=1.0,
        reason="trigger",
        profiles=[dialog.row_widgets[0]]
    )
    assert not dialog.result_badge.tag_lbl.isHidden()
    assert "ТРИГГЕР СТИЛЯ" in dialog.result_badge.tag_lbl.text()

    # 4. Ошибка роутинга
    dialog._on_test_match_failed("Тестовая ошибка роутинга")
    assert "Ошибка роутинга" in dialog.result_badge.text()

    dialog.reject()


def test_models_manager_save_all_flow(qapp, isolated_storage):
    """Проверяет сохранение всех профилей в art_director_models.json и вызов синхронизации."""
    tmp_json_path = isolated_storage
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    dialog.input_name.setText("Masterpiece SDXL")
    dialog._save_form_to_model(0)

    with patch.object(QMessageBox, "information"), \
         patch("ui.models_manager_dialog.sync_registry_with_user_config") as mock_sync:
        dialog._save_all()
        assert mock_sync.called

    # Проверяем, что на диск записались данные
    saved_profiles = load_art_director_models()
    assert len(saved_profiles) == 2
    assert saved_profiles[0]["name"] == "Masterpiece SDXL"

    dialog.reject()


def test_models_manager_safe_shutdown_and_worker_cleanup(qapp, isolated_storage):
    """Проверяет остановку фоновых воркеров при закрытии диалога."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    mock_scan_worker = MagicMock()
    mock_scan_worker.isRunning.return_value = False
    mock_test_worker = MagicMock()
    mock_test_worker.isRunning.return_value = False

    dialog._scan_worker = mock_scan_worker
    dialog._test_worker = mock_test_worker

    dialog.reject()

    assert mock_scan_worker.abort.called or mock_scan_worker.quit.called
    assert mock_test_worker.abort.called or mock_test_worker.quit.called
    assert dialog._scan_worker is None
    assert dialog._test_worker is None
