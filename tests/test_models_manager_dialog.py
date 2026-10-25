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
)
from qfluentwidgets import PushButton, PrimaryPushButton, TransparentToolButton


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
