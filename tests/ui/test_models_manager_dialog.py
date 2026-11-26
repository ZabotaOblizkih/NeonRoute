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
    """Проверяет обработку полученных параметров, подтверждение перезаписи и фиксацию target_row."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    dialog._autofill_target_row = 0
    # Переключаем текущую строку на 1 во время «фонового» запроса — изменения должны лечь в row 0
    dialog.table.selectRow(1)
    dialog.current_selected_row = 1

    civitai_response = {
        "status": "success",
        "sampler": "Euler a",
        "scheduler": "Normal",
        "architecture": "sdxl",
        "steps": 30,
        "cfg_scale": 7.0,
        "clip_skip": 2,
        "hr_scale": 1.35,
        "hires_upscaler": "4x-AnimeSharp",
        "hires_denoise": 0.40,
        "negative_prompt": "civitai negative sample",
        "style_triggers": ["anime", "cel"],
        "trained_words": ["1girl", "solo"],
    }

    mock_msg = MagicMock()
    mock_msg.exec.return_value = True
    with patch("ui.models_manager_dialog.InfoBar"), patch("ui.models_manager_dialog.MessageBox", return_value=mock_msg):
        dialog._on_civitai_params_ready(civitai_response)

    # Целевая строка 0 обновлена полностью, несмотря на смену current_selected_row на 1
    row0 = dialog.row_widgets[0]
    assert row0["pipeline"]["sampler"] == "Euler a"
    assert row0["pipeline"]["scheduler"] == "Normal"
    assert row0["pipeline"]["steps"] == 30
    assert row0["pipeline"]["hr_scale"] == 1.35
    assert row0["pipeline"]["negative_prompt"] == "civitai negative sample"
    assert row0["style_triggers"] == ["anime", "manga", "cel"]  # без дублей
    assert row0["trained_words"] == ["1girl", "solo"]
    # Строка 1 не испорчена
    assert dialog.row_widgets[1]["pipeline"]["steps"] == 25

    # Проверяем отказ от перезаписи ручных полей при empty_meta
    dialog._autofill_target_row = 1
    decline_msg = MagicMock()
    decline_msg.exec.return_value = False
    with patch("ui.models_manager_dialog.InfoBar"), patch("ui.models_manager_dialog.MessageBox", return_value=decline_msg):
        dialog._on_civitai_params_ready({"status": "empty_meta", "steps": 12, "negative_prompt": "should not apply"})
    assert dialog.row_widgets[1]["pipeline"]["steps"] == 25
    assert dialog.row_widgets[1]["negative_prompt"] == "bad anatomy, doll"

    dialog.reject()


def test_civitai_partial_profile_e2e_preserves_manual_style_and_prompts(qapp, isolated_storage, tmp_path, monkeypatch):
    """
    Сквозной смок-тест (resolve_model_presets + _on_civitai_params_ready):
    - модели нет в каталоге (GitHub/Local);
    - Civitai возвращает технические параметры (steps, cfgScale, sampler, Clip skip),
      но trainedWords пуст, negativePrompt отсутствует, а стилевых полей нет;
    - пользовательские routing_description, llm_guidance, style_triggers и negative_prompt
      в UI сохраняются нетронутыми без лишних диалогов.
    """
    from services.model_catalog_service import resolve_model_presets

    empty_catalog = tmp_path / "empty_catalog.json"
    empty_catalog.write_text('{"schema_version": 1, "generated_at": "2026-11-25T00:00:00Z", "models": []}', encoding="utf-8")

    civitai_ver_resp = MagicMock()
    civitai_ver_resp.status_code = 200
    civitai_ver_resp.json.return_value = {
        "items": [
            {
                "name": "SDXL Base Anime",
                "tags": ["anime"],
                "modelVersions": [{"id": 999, "name": "v1.0", "baseModel": "SDXL 1.0"}],
            }
        ]
    }
    civitai_detail_resp = MagicMock()
    civitai_detail_resp.status_code = 200
    civitai_detail_resp.json.return_value = {
        "id": 999,
        "name": "v1.0",
        "baseModel": "SDXL 1.0",
        "trainedWords": [],
        "images": [
            {
                "meta": {
                    "sampler": "DPM++ 2M Karras",
                    "steps": 32,
                    "cfgScale": 6.5,
                    "Clip skip": 2,
                }
            }
        ],
    }

    def _mock_get(url, *args, **kwargs):
        if "model-versions/999" in url:
            return civitai_detail_resp
        return civitai_ver_resp

    monkeypatch.setattr("services.civitai_service.requests.get", _mock_get)
    monkeypatch.setattr("services.civitai_service.resolve_checkpoint_file", lambda _: None)

    resolved = resolve_model_presets(
        "sdxl_anime_v1.safetensors",
        allow_github=False,
        catalog_path=empty_catalog,
    )
    assert resolved["status"] == "success"
    assert resolved["source"] == "civitai"
    assert resolved["negative_prompt"] is None
    assert resolved["routing_description"] is None
    assert resolved["llm_guidance"] is None
    assert resolved["style_triggers"] == []
    assert resolved["trained_words"] == []

    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    dialog._autofill_target_row = 0
    # Не должно вызываться ни одного MessageBox при применении частичного профиля
    with patch("ui.models_manager_dialog.InfoBar"), patch("ui.models_manager_dialog.MessageBox") as mock_mb:
        dialog._on_civitai_params_ready(resolved)
        mock_mb.assert_not_called()

    # Технические параметры применились
    assert dialog.combo_sampler.currentText() == "DPM++ 2M"
    assert dialog.combo_scheduler.currentText() == "Karras"
    assert dialog.spin_steps.value() == 32
    assert abs(dialog.spin_cfg.value() - 6.5) < 0.05
    assert dialog.spin_clip_skip.value() == 2
    assert dialog.row_widgets[0]["pipeline"]["steps"] == 32

    # Пользовательские текстовые/стилевые поля сохранились нетронутыми
    assert dialog.input_desc.toPlainText() == "Аниме стилистика SDXL"
    assert dialog.input_guidance.toPlainText() == "anime, masterpiece"
    assert dialog.input_negative_prompt.toPlainText() == "worst quality, blurry"
    assert dialog.row_widgets[0]["style_triggers"] == ["anime", "manga"]
    assert dialog.row_widgets[0]["negative_prompt"] == "worst quality, blurry"

    dialog.reject()


def test_autofill_logging_correlation_id_stages(qapp, isolated_storage, tmp_path, monkeypatch, caplog):
    """
    Проверяет сквозной correlation ID (autofill_id) по всем этапам логирования:
    NORMALIZE -> HASH_SKIPPED -> GITHUB_CATALOG -> LOCAL_CATALOG -> CIVITAI_API -> UI_READY -> UI_APPLY.
    """
    import logging
    from services.model_catalog_service import resolve_model_presets

    caplog.set_level(logging.INFO)
    empty_catalog = tmp_path / "empty_cat.json"
    empty_catalog.write_text('{"schema_version": 1, "generated_at": "2026-11-25T00:00:00Z", "models": []}', encoding="utf-8")

    search_resp = MagicMock(status_code=200)
    search_resp.json.return_value = {
        "items": [{"name": "SDXL Base Anime", "tags": [], "modelVersions": [{"id": 777, "name": "v1.0", "baseModel": "SDXL 1.0"}]}]
    }
    ver_resp = MagicMock(status_code=200)
    ver_resp.json.return_value = {
        "id": 777,
        "name": "v1.0",
        "baseModel": "SDXL 1.0",
        "trainedWords": [],
        "images": [{"meta": {"sampler": "Euler a", "steps": 24, "cfgScale": 6.0}}],
    }

    monkeypatch.setattr("services.model_catalog_service.requests.get", lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("offline")))
    monkeypatch.setattr("services.civitai_service.requests.get", lambda url, *a, **kw: ver_resp if "model-versions/777" in url else search_resp)
    monkeypatch.setattr("services.civitai_service.resolve_checkpoint_file", lambda _: None)

    aid = "trace99a"
    resolved = resolve_model_presets(
        "sdxl_anime_v1.safetensors",
        catalog_url="https://example.test/cat.json",
        catalog_path=empty_catalog,
        autofill_id=aid,
    )
    assert resolved["autofill_id"] == aid

    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    dialog._autofill_target_row = 0
    with patch("ui.models_manager_dialog.InfoBar"), patch("ui.models_manager_dialog.MessageBox"):
        dialog._on_civitai_params_ready(resolved)

    dialog.reject()

    aid_logs = [rec.getMessage() for rec in caplog.records if f"[AutoFill:{aid}]" in rec.getMessage()]
    joined = "\n".join(aid_logs)
    for expected_stage in (
        "NORMALIZE",
        "HASH_SKIPPED",
        "GITHUB_CATALOG status=error",
        "LOCAL_CATALOG status=miss",
        "CIVITAI_API",
        "UI_READY",
        "UI_APPLY",
    ):
        assert expected_stage in joined, f"Missing stage '{expected_stage}' in logs:\n{joined}"
    assert "preserved_manual=['routing_description', 'llm_guidance', 'negative_prompt', 'style_triggers']" in joined


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


def test_models_manager_civitai_model_installed_html_cleaning(qapp, isolated_storage):
    """Проверяет очистку HTML-тегов и Hangul filler при авторегистрации скачанной с Civitai модели."""
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    model_info = {
        "id": "9988",
        "name": "Civitai Anime Master",
        "filename": "civitai_anime_v1.safetensors",
        "description": "<p>Ultra anime model with <strong>sharp</strong> details.</p>ᅠ",
        "triggers": ["masterpiece", "anime"],
        "sampler": "Euler a",
        "steps": 28,
        "cfg_scale": 7.0
    }

    dialog._on_civitai_model_installed(model_info)

    # Модель должна добавиться в row_widgets
    added = [r for r in dialog.row_widgets if r.get("checkpoint") == "civitai_anime_v1.safetensors"]
    assert len(added) == 1
    routing_desc = added[0].get("routing_description", "")

    # Проверка, что сырой HTML и \u3164 вычищены
    assert "<p>" not in routing_desc
    assert "<strong>" not in routing_desc
    assert "\u3164" not in routing_desc
    assert "Ultra anime model with sharp details." in routing_desc

    dialog.reject()


def test_clean_release_starts_empty_and_populates_from_forge_scan(qapp, isolated_storage):
    """
    При отсутствии data/art_director_models.json (чистый релиз):
    1. load_art_director_models() и ModelRouter.load_profiles() возвращают [] и не создают файл с дефолтами.
    2. ModelsManagerDialog открывается с пустой таблицей и пустым списком чекпоинтов.
    3. После сканирования Forge добавляются только реально найденные чекпоинты.
    """
    from services.art_director_config import load_art_director_models
    from services.model_router import ModelRouter

    if isolated_storage.exists():
        isolated_storage.unlink()

    assert load_art_director_models() == []
    assert not isolated_storage.exists()

    ModelRouter.reset_instance()
    router = ModelRouter(data_path=isolated_storage, auto_load=True)
    assert router.load_profiles() == []
    assert not isolated_storage.exists()

    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    assert dialog.table.rowCount() == 0
    assert dialog.row_widgets == []
    assert dialog.available_checkpoints == []

    # Имитируем сканирование Forge, нашедшее 1 реальный чекпоинт
    dialog._scan_silent = True
    dialog._on_forge_scan_completed(
        raw_models=[{"title": "my_custom_sdxl.safetensors", "filename": "C:/Forge/models/my_custom_sdxl.safetensors"}],
        installed_list=["my_custom_sdxl.safetensors"],
        upscalers=[],
    )

    assert dialog.table.rowCount() == 1
    assert dialog.row_widgets[0]["checkpoint"] == "my_custom_sdxl.safetensors"
    saved = load_art_director_models()
    assert [m["checkpoint"] for m in saved] == ["my_custom_sdxl.safetensors"]

    dialog.reject()


