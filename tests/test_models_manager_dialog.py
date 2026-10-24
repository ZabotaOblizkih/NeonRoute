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
