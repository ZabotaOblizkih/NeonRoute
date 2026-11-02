"""
Unit tests for modernized ModelsManagerDialog in PyQt6 and QFluentWidgets.
"""

import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt

from ui.models_manager_dialog import (
    ModelsManagerDialog,
    RoutingResultBadge,
    FluentEditableComboBox,
    FluentTextEdit,
    ForgeScanWorker,
    RoutingTestWorker,
    CivitaiParamsWorker,
    FP8ConvertWorker,
)
from qfluentwidgets import PushButton, PrimaryPushButton, TransparentToolButton, CheckBox, ProgressBar


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def mock_forge_client():
    client = MagicMock()
    client.get_models.return_value = [
        {"title": "cyberrealistic_classic.safetensors", "model_name": "cyberrealistic"}
    ]
    client.get_installed_checkpoints.return_value = [
        "C:/forge/models/Stable-diffusion/cyberrealistic_classic.safetensors"
    ]
    client.get_upscalers.return_value = ["4x-UltraSharp", "DAT x2"]
    return client


class TestModelsManagerDialog:
    def test_dialog_initialization_and_components(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        assert dlg.windowTitle().startswith("🎨 Модели генерации")
        assert dlg.table.columnCount() == 4
        assert isinstance(dlg.btn_refresh_forge, PushButton)
        assert isinstance(dlg.btn_civitai, PushButton)
        assert isinstance(dlg.btn_add_model, PushButton)
        assert isinstance(dlg.btn_save, PrimaryPushButton)
        assert isinstance(dlg.btn_close, PushButton)
        assert isinstance(dlg.result_badge, RoutingResultBadge)
        assert isinstance(dlg.combo_checkpoint, FluentEditableComboBox)
        assert isinstance(dlg.input_negative_prompt, FluentTextEdit)
        assert isinstance(dlg.input_desc, FluentTextEdit)
        assert isinstance(dlg.input_guidance, FluentTextEdit)

    def test_slider_spinbox_bidirectional_sync(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        # 1. Steps
        dlg.slider_steps.setValue(35)
        assert dlg.spin_steps.value() == 35
        dlg.spin_steps.setValue(42)
        assert dlg.slider_steps.value() == 42

        # 2. CFG Scale
        dlg.slider_cfg.setValue(75)
        assert dlg.spin_cfg.value() == 7.5
        dlg.spin_cfg.setValue(9.0)
        assert dlg.slider_cfg.value() == 90

        # 3. Clip Skip
        dlg.slider_clip_skip.setValue(2)
        assert dlg.spin_clip_skip.value() == 2
        dlg.spin_clip_skip.setValue(1)
        assert dlg.slider_clip_skip.value() == 1

        # 4. Hires Denoise
        dlg.slider_hires_denoise.setValue(45)
        assert abs(dlg.spin_hires_denoise.value() - 0.45) < 0.001
        dlg.spin_hires_denoise.setValue(0.28)
        assert dlg.slider_hires_denoise.value() == 28

    def test_routing_result_badge_states(self, qapp):
        badge = RoutingResultBadge()
        assert badge.icon_lbl.text() == "💡"

        badge.set_loading()
        assert badge.icon_lbl.text() == "⏳"

        badge.set_success("CyberRealistic", "cyber.safetensors", "trigger", 0.95)
        assert badge.icon_lbl.text() == "🎯"
        assert not badge.tag_lbl.isHidden()
        assert "ТРИГГЕР" in badge.tag_lbl.text()

        badge.set_success("Photoreal", "photo.safetensors", "vector", 0.88)
        assert "FASTEMBED" in badge.tag_lbl.text()

        badge.set_error("Тестовая ошибка")
        assert badge.icon_lbl.text() == "❌"
        assert badge.tag_lbl.isHidden()

    def test_table_row_add_and_delete(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        initial_count = dlg.table.rowCount()
        dlg._add_empty_model_row()
        assert dlg.table.rowCount() == initial_count + 1

        # Delete without prompt
        new_row_info = dlg.row_widgets[-1]
        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dlg._remove_model_by_id(new_row_info["id"])
        assert dlg.table.rowCount() == initial_count

    def test_backward_compatibility_aliases(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        assert hasattr(dlg, "_scan_forge_checkpoints")
        assert hasattr(dlg, "_on_test_prompt_clicked")
        assert hasattr(dlg, "_on_test_routing_clicked")
        assert hasattr(dlg, "_run_test_query")
        assert dlg.lbl_test_result is dlg.result_badge
        assert dlg.test_result_label is dlg.result_badge

    def test_models_manager_stop_all_workers_on_close_and_reject(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        dlg._scan_worker = mock_worker

        mock_test_worker = MagicMock()
        mock_test_worker.isRunning.return_value = False
        dlg._test_worker = mock_test_worker

        dlg._stop_all_workers()

        mock_worker.abort.assert_called_once()
        mock_worker.quit.assert_called_once()
        mock_worker.wait.assert_called_once_with(200)
        mock_worker.setParent.assert_called_once_with(None)

        assert dlg._scan_worker is None
        assert dlg._test_worker is None

    def test_civitai_search_worker_abort(self):
        from ui.dialogs.civitai_catalog_dialog import CivitaiSearchWorker
        worker = CivitaiSearchWorker("test", None, None, "Most Downloaded", False, 1)
        assert not worker._is_aborted
        worker.abort()
        assert worker._is_aborted

    def test_civitai_catalog_stop_all_workers(self, qapp):
        from ui.dialogs.civitai_catalog_dialog import CivitaiCatalogDialog
        with patch.object(CivitaiCatalogDialog, "_on_search_clicked"):
            dlg = CivitaiCatalogDialog()

        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        dlg._search_worker = mock_worker

        dlg._stop_all_workers()

        mock_worker.abort.assert_called_once()
        mock_worker.quit.assert_called_once()
        mock_worker.wait.assert_called_once_with(300)
        mock_worker.setParent.assert_called_once_with(None)
        assert dlg._search_worker is None

    def test_sdxl_and_pipeline_controls_initialization(self, qapp, mock_forge_client):
        from qfluentwidgets import SwitchButton, ComboBox
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        # 1. ComboBox architecture
        assert isinstance(dlg.combo_arch, ComboBox)
        arch_items = [dlg.combo_arch.itemText(i) for i in range(dlg.combo_arch.count())]
        assert "SDXL / Pony" in arch_items
        assert "SD 1.5" in arch_items

        # 2. ComboBox scheduler
        assert isinstance(dlg.combo_scheduler, ComboBox)
        sched_items = [dlg.combo_scheduler.itemText(i) for i in range(dlg.combo_scheduler.count())]
        assert "Karras" in sched_items
        assert "SGM Uniform" in sched_items
        assert "Exponential" in sched_items
        assert "Normal" in sched_items
        assert "Simple" in sched_items

        # 3. ComboBox sampler with LCM
        sampler_items = [dlg.combo_sampler.itemText(i) for i in range(dlg.combo_sampler.count())]
        assert "LCM" in sampler_items

        # 4. Steps range 4 to 60
        assert dlg.spin_steps.minimum() == 4
        assert dlg.spin_steps.maximum() == 60
        assert dlg.slider_steps.minimum() == 4
        assert dlg.slider_steps.maximum() == 60

        # 5. Switches
        assert isinstance(dlg.switch_enable_hr, SwitchButton)
        assert isinstance(dlg.switch_enable_ad, SwitchButton)

        # 6. Presets ComboBox
        assert isinstance(dlg.combo_presets, ComboBox)
        preset_items = [dlg.combo_presets.itemText(i) for i in range(dlg.combo_presets.count())]
        assert "Пользовательский" in preset_items[0]
        assert "SDXL Turbo / DMD2" in preset_items[1]
        assert "SDXL Classic / Pony" in preset_items[2]
        assert "SD 1.5 Photoreal" in preset_items[3]

    def test_presets_reactive_selection(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        # Preset 1: SDXL Turbo / DMD2
        dlg.combo_presets.setCurrentIndex(1)
        assert dlg.combo_arch.currentText() == "SDXL / Pony"
        assert dlg.combo_sampler.currentText() == "LCM"
        assert dlg.combo_scheduler.currentText() == "Karras"
        assert dlg.spin_steps.value() == 10
        assert dlg.slider_steps.value() == 10
        assert abs(dlg.spin_cfg.value() - 1.3) < 0.05
        assert dlg.spin_clip_skip.value() == 2
        assert dlg.switch_enable_hr.isChecked() is False
        assert dlg.switch_enable_ad.isChecked() is False

        # Preset 2: SDXL Classic / Pony
        dlg.combo_presets.setCurrentIndex(2)
        assert dlg.combo_arch.currentText() == "SDXL / Pony"
        assert dlg.combo_sampler.currentText() == "DPM++ 2M"
        assert dlg.combo_scheduler.currentText() == "Karras"
        assert dlg.spin_steps.value() == 28
        assert dlg.slider_steps.value() == 28
        assert abs(dlg.spin_cfg.value() - 5.0) < 0.05
        assert dlg.spin_clip_skip.value() == 2
        assert dlg.switch_enable_hr.isChecked() is False
        assert dlg.switch_enable_ad.isChecked() is False

        # Preset 3: SD 1.5 Photoreal
        dlg.combo_presets.setCurrentIndex(3)
        assert dlg.combo_arch.currentText() == "SD 1.5"
        assert dlg.combo_sampler.currentText() == "DPM++ SDE Karras"
        assert dlg.combo_scheduler.currentText() == "Karras"
        assert dlg.spin_steps.value() == 25
        assert dlg.slider_steps.value() == 25
        assert abs(dlg.spin_cfg.value() - 5.5) < 0.05
        assert dlg.spin_clip_skip.value() == 1
        assert dlg.switch_enable_hr.isChecked() is True
        assert dlg.switch_enable_ad.isChecked() is True

    def test_data_lifecycle_load_and_save_architecture_scheduler_switches(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        # Mock model with custom architecture and scheduler
        test_model = {
            "id": "test_sdxl",
            "name": "Test SDXL Model",
            "architecture": "sdxl",
            "checkpoint": "test_sdxl.safetensors",
            "pipeline": {
                "architecture": "sdxl",
                "sampler": "LCM",
                "scheduler": "SGM Uniform",
                "steps": 12,
                "cfg_scale": 1.5,
                "clip_skip": 2,
                "enable_hr": True,
                "enable_adetailer": False
            }
        }
        dlg.row_widgets = [test_model]
        dlg._load_model_to_form(0)

        assert dlg.combo_arch.currentText() == "SDXL / Pony"
        assert dlg.combo_scheduler.currentText() == "SGM Uniform"
        assert dlg.combo_sampler.currentText() == "LCM"
        assert dlg.spin_steps.value() == 12
        assert dlg.switch_enable_hr.isChecked() is True
        assert dlg.switch_enable_ad.isChecked() is False

        # Modify values in form and save
        dlg.combo_arch.setCurrentText("SD 1.5")
        dlg.combo_scheduler.setCurrentText("Exponential")
        dlg.switch_enable_hr.setChecked(False)
        dlg.switch_enable_ad.setChecked(True)
        dlg._save_form_to_model(0)

        saved = dlg.row_widgets[0]
        assert saved["architecture"] == "sd15"
        assert saved["pipeline"]["architecture"] == "sd15"
        assert saved["pipeline"]["scheduler"] == "Exponential"
        assert saved["pipeline"]["enable_hr"] is False
        assert saved["pipeline"]["enable_adetailer"] is True

    def test_forge_scan_auto_registration_sdxl_vs_sd15(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        dlg.row_widgets = []
        raw_models = []
        installed = [
            "C:/forge/models/AnimagineXL_v3.safetensors",
            "C:/forge/models/realistic_vision_v51.safetensors"
        ]
        dlg._on_forge_scan_completed(raw_models, installed, [])

        assert len(dlg.row_widgets) == 2
        xl_model = next(r for r in dlg.row_widgets if "animagine" in r["checkpoint"].lower())
        sd15_model = next(r for r in dlg.row_widgets if "realistic" in r["checkpoint"].lower())

        # SDXL model initialized with SDXL Turbo profile
        assert xl_model["architecture"] == "sdxl"
        assert xl_model["pipeline"]["sampler"] == "LCM"
        assert xl_model["pipeline"]["scheduler"] == "Karras"
        assert xl_model["pipeline"]["steps"] == 10
        assert xl_model["pipeline"]["cfg_scale"] == 1.3
        assert xl_model["pipeline"]["enable_hr"] is False
        assert xl_model["pipeline"]["enable_adetailer"] is False

        # SD 1.5 model initialized with standard SD 1.5 defaults
        assert sd15_model["architecture"] == "sd15"
        assert sd15_model["pipeline"]["sampler"] == "DPM++ 2M Karras"
        assert sd15_model["pipeline"]["steps"] == 25

    def test_art_director_models_json_utf8_sig_encoding(self, tmp_path):
        import json
        from services.art_director_config import save_art_director_models, load_art_director_models
        from unittest.mock import patch

        test_file = tmp_path / "art_director_models.json"
        test_data = [{"id": "test_m", "name": "Модель с кириллицей", "checkpoint": "m.safetensors"}]

        with patch("services.art_director_config.ART_DIRECTOR_MODELS_PATH", test_file):
            save_art_director_models(test_data)
            assert test_file.exists()
            raw_bytes = test_file.read_bytes()
            assert raw_bytes.startswith(b'\xef\xbb\xbf')

            loaded = load_art_director_models()
            assert loaded[0]["name"] == "Модель с кириллицей"

    def test_fp8_controls_initialization(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        assert isinstance(dlg.btn_convert_fp8, PushButton)
        assert "FP8" in dlg.btn_convert_fp8.text()
        assert "UNet" in dlg.btn_convert_fp8.toolTip()
        assert isinstance(dlg.chk_delete_source, CheckBox)
        assert dlg.chk_delete_source.isChecked() is True
        assert isinstance(dlg.progress_fp8, ProgressBar)
        assert dlg.progress_fp8.isHidden()
        assert dlg.lbl_fp8_status.isHidden()

    def test_fp8_button_state_updates(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        # 1. Normal safetensors -> enabled
        dlg.combo_checkpoint.setEditText("sdxl_model.safetensors")
        dlg._update_fp8_button_state()
        assert dlg.btn_convert_fp8.isEnabled() is True

        # 2. Already converted _fp8e4 -> disabled
        dlg.combo_checkpoint.setEditText("sdxl_model_fp8e4.safetensors")
        dlg._update_fp8_button_state()
        assert dlg.btn_convert_fp8.isEnabled() is False

        # 3. Not a safetensors file -> disabled
        dlg.combo_checkpoint.setEditText("model.ckpt")
        dlg._update_fp8_button_state()
        assert dlg.btn_convert_fp8.isEnabled() is False

        # 4. Empty -> disabled
        dlg.combo_checkpoint.setEditText("")
        dlg._update_fp8_button_state()
        assert dlg.btn_convert_fp8.isEnabled() is False

        # 5. Worker running -> disabled regardless of filename
        dlg.combo_checkpoint.setEditText("sdxl_model.safetensors")
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        dlg._fp8_worker = mock_worker
        dlg._update_fp8_button_state()
        assert dlg.btn_convert_fp8.isEnabled() is False

    def test_resolve_converter_script(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        script = dlg._resolve_converter_script()
        assert script is not None
        assert script.name == "convert_sdxl_to_fp8.py"
        assert script.is_file()

    def test_resolve_selected_checkpoint_path(self, qapp, mock_forge_client, tmp_path):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        # 1. Empty name -> None
        assert dlg._resolve_selected_checkpoint_path("") is None

        # 2. Match from forge_client.get_installed_checkpoints()
        fake_ckpt = tmp_path / "installed_xl.safetensors"
        fake_ckpt.write_text("dummy")
        dlg.forge_client.get_installed_checkpoints.return_value = [str(fake_ckpt)]
        found = dlg._resolve_selected_checkpoint_path("installed_xl.safetensors")
        assert found == fake_ckpt.resolve()

        # 3. Match from forge_client.get_models()
        fake_model = tmp_path / "model_api_xl.safetensors"
        fake_model.write_text("dummy")
        dlg.forge_client.get_installed_checkpoints.return_value = []
        dlg.forge_client.get_models.return_value = [{"filename": str(fake_model)}]
        found_model = dlg._resolve_selected_checkpoint_path("model_api_xl.safetensors")
        assert found_model == fake_model.resolve()

    def test_resolve_torch_python(self, qapp, mock_forge_client, tmp_path):
        from pathlib import Path
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        # 1. Verified python found in candidate
        fake_python = tmp_path / "python.exe"
        fake_python.write_text("dummy")
        launcher = tmp_path / "forge_launcher.bat"
        launcher.write_text(f'set PYTHON="{fake_python}"\n')
        with patch.object(dlg, "_verify_python", return_value=True):
            with patch("config.FORGE_ROOT_DIR", tmp_path), \
                 patch("config.FORGE_LAUNCHER_PATH", launcher):
                result = dlg._resolve_torch_python()
                assert result == str(fake_python)

        # 2. None found, user cancels QFileDialog
        with patch.object(dlg, "_verify_python", return_value=False):
            with patch("PyQt6.QtWidgets.QFileDialog.getOpenFileName", return_value=("", "")):
                assert dlg._resolve_torch_python() is None

    def test_fp8_convert_worker_signals_and_abort(self, tmp_path):
        fake_script = tmp_path / "script.py"
        fake_script.write_text("print('start')")
        fake_input = tmp_path / "model.safetensors"
        fake_input.write_text("dummy")

        worker = FP8ConvertWorker("python.exe", fake_script, fake_input)
        assert worker.output_path == tmp_path / "model_fp8e4.safetensors"

        # Test abort before run
        worker.abort()
        assert worker._is_aborted is True

        # Test run with mocked subprocess.Popen
        mock_proc = MagicMock()
        mock_proc.poll.side_effect = [None, None, 0]
        mock_proc.stdout = [
            "[10/30] UNet FP8 converted\n",
            "Done!\n"
        ]
        mock_proc.wait.return_value = 0

        worker2 = FP8ConvertWorker("python.exe", fake_script, fake_input)
        # Create output file so check passes
        worker2.output_path.write_text("fp8_dummy")

        progress_signals = []
        finished_signals = []
        failed_signals = []

        worker2.progress_updated.connect(progress_signals.append)
        worker2.conversion_finished.connect(finished_signals.append)
        worker2.conversion_failed.connect(failed_signals.append)

        with patch("subprocess.Popen", return_value=mock_proc):
            worker2.run()

        assert len(progress_signals) == 2
        assert "[10/30]" in progress_signals[0]
        assert "Done!" in progress_signals[1]
        assert len(finished_signals) == 1
        assert finished_signals[0] == str(worker2.output_path)
        assert len(failed_signals) == 0

    def test_on_start_fp8_conversion_validation(self, qapp, mock_forge_client, tmp_path):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        with patch("qfluentwidgets.InfoBar.warning") as mock_warn, \
             patch("qfluentwidgets.InfoBar.error") as mock_err:

            # 1. No checkpoint selected
            dlg.combo_checkpoint.setEditText("")
            dlg._on_start_fp8_conversion()
            mock_warn.assert_called_once()
            assert dlg._fp8_worker is None

            # 2. Script not found
            mock_warn.reset_mock()
            dlg.combo_checkpoint.setEditText("model.safetensors")
            with patch.object(dlg, "_resolve_converter_script", return_value=None):
                dlg._on_start_fp8_conversion()
                mock_err.assert_called_once()
                assert dlg._fp8_worker is None

            # 3. Checkpoint file not found on disk
            mock_err.reset_mock()
            with patch.object(dlg, "_resolve_converter_script", return_value=tmp_path / "c.py"), \
                 patch.object(dlg, "_resolve_selected_checkpoint_path", return_value=None):
                dlg._on_start_fp8_conversion()
                mock_err.assert_called_once()
                assert dlg._fp8_worker is None

            # 4. Python not found
            mock_err.reset_mock()
            fake_ckpt = tmp_path / "model.safetensors"
            fake_ckpt.write_text("dummy")
            with patch.object(dlg, "_resolve_converter_script", return_value=tmp_path / "c.py"), \
                 patch.object(dlg, "_resolve_selected_checkpoint_path", return_value=fake_ckpt), \
                 patch.object(dlg, "_resolve_torch_python", return_value=None):
                dlg._on_start_fp8_conversion()
                mock_warn.assert_called_once()
                assert dlg._fp8_worker is None

            # 5. Success path starts worker
            with patch.object(dlg, "_resolve_converter_script", return_value=tmp_path / "c.py"), \
                 patch.object(dlg, "_resolve_selected_checkpoint_path", return_value=fake_ckpt), \
                 patch.object(dlg, "_resolve_torch_python", return_value="python.exe"), \
                 patch.object(FP8ConvertWorker, "start") as mock_worker_start:
                dlg._on_start_fp8_conversion()
                assert dlg._fp8_worker is not None
                mock_worker_start.assert_called_once()
                assert dlg.btn_convert_fp8.isEnabled() is False
                assert not dlg.progress_fp8.isHidden()
                assert not dlg.lbl_fp8_status.isHidden()

    def test_on_fp8_progress(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        dlg._on_fp8_progress("[14/30] UNet block 14")
        assert dlg.lbl_fp8_status.text() == "[14/30] UNet block 14"
        assert dlg.progress_fp8.maximum() == 30
        assert dlg.progress_fp8.value() == 14

    def test_on_fp8_finished_and_delete_source(self, qapp, mock_forge_client, tmp_path):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        orig_file = tmp_path / "animagine_xl.safetensors"
        orig_file.write_text("orig_content")
        new_file = tmp_path / "animagine_xl_fp8e4.safetensors"
        new_file.write_text("new_fp8_content")

        dlg.available_checkpoints = ["animagine_xl.safetensors"]
        dlg.combo_checkpoint.setEditText("animagine_xl.safetensors")
        dlg.chk_delete_source.setChecked(True)

        # Set up a selected model row
        dlg.row_widgets = [{
            "id": "model_1",
            "name": "Animagine XL",
            "checkpoint": "animagine_xl.safetensors",
            "pipeline": {}
        }]
        dlg.current_selected_row = 0

        with patch.object(dlg, "_resolve_selected_checkpoint_path", return_value=orig_file), \
             patch("qfluentwidgets.InfoBar.success") as mock_success:
            dlg._on_fp8_finished(str(new_file))

            mock_forge_client.refresh_checkpoints.assert_called_once()
            # Original file deleted
            assert not orig_file.exists()
            # Checkpoint updated in model and available list
            assert "animagine_xl_fp8e4.safetensors" in dlg.available_checkpoints
            assert dlg.row_widgets[0]["checkpoint"] == "animagine_xl_fp8e4.safetensors"
            assert dlg.combo_checkpoint.currentText() == "animagine_xl_fp8e4.safetensors"
            assert dlg.btn_convert_fp8.isEnabled() is False  # Because it's _fp8e4
            assert dlg.progress_fp8.isHidden()
            mock_success.assert_called_once()

    def test_on_fp8_finished_without_delete_source(self, qapp, mock_forge_client, tmp_path):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        orig_file = tmp_path / "pony_xl.safetensors"
        orig_file.write_text("orig_content")
        new_file = tmp_path / "pony_xl_fp8e4.safetensors"
        new_file.write_text("new_fp8_content")

        dlg.available_checkpoints = ["pony_xl.safetensors"]
        dlg.combo_checkpoint.setEditText("pony_xl.safetensors")
        dlg.chk_delete_source.setChecked(False)

        dlg.row_widgets = [{
            "id": "model_2",
            "name": "Pony XL",
            "checkpoint": "pony_xl.safetensors",
            "pipeline": {}
        }]
        dlg.current_selected_row = 0

        with patch.object(dlg, "_resolve_selected_checkpoint_path", return_value=orig_file), \
             patch("qfluentwidgets.InfoBar.success"):
            dlg._on_fp8_finished(str(new_file))

            # Original file preserved
            assert orig_file.exists()
            assert "pony_xl_fp8e4.safetensors" in dlg.available_checkpoints
            assert dlg.row_widgets[0]["checkpoint"] == "pony_xl_fp8e4.safetensors"

    def test_on_fp8_failed(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        dlg._fp8_worker = MagicMock()
        dlg.btn_convert_fp8.setEnabled(False)
        dlg.progress_fp8.setVisible(True)

        with patch("qfluentwidgets.InfoBar.error") as mock_err:
            dlg._on_fp8_failed("CUDA Out of Memory")
            mock_err.assert_called_once()
            assert dlg._fp8_worker is None
            assert dlg.progress_fp8.isHidden()
            assert dlg.combo_checkpoint.isEnabled() is True

    def test_stop_all_workers_with_fp8_worker(self, qapp, mock_forge_client):
        with patch.object(ModelsManagerDialog, "_start_forge_scan"):
            dlg = ModelsManagerDialog(forge_client=mock_forge_client)

        mock_fp8 = MagicMock()
        mock_fp8.isRunning.return_value = True
        dlg._fp8_worker = mock_fp8

        dlg._stop_all_workers()

        mock_fp8.abort.assert_called_once()
        mock_fp8.quit.assert_called_once()
        mock_fp8.wait.assert_called_once_with(200)
        assert dlg._fp8_worker is None
