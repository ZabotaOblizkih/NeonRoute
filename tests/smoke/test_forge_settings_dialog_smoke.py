"""
Smoke test for ForgeSettingsDialog.
Verifies dialog creation, lazy import via ui.dialogs, initial UI states,
patch check button, and clean save execution in headless environment.
"""

import pytest
from pathlib import Path
from PyQt6.QtWidgets import QWidget
from ui.dialogs import ForgeSettingsDialog
from utils.config_manager import ConfigManager


def test_forge_settings_dialog_lazy_import():
    """Verifies ForgeSettingsDialog can be imported via PEP 562 lazy loading in ui.dialogs."""
    from ui.dialogs import ForgeSettingsDialog as LazyDialog
    assert LazyDialog is not None
    assert LazyDialog.__name__ == "ForgeSettingsDialog"


def test_forge_settings_dialog_initialization_and_ui_elements(qapp, tmp_path, monkeypatch):
    """Verifies dialog initializes without crash and binds UI elements cleanly."""
    # Use isolated config file in tmp_path
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"forge_sdxl_fp8_enabled": true, "auto_generate_art": false}', encoding="utf-8")
    monkeypatch.setattr("utils.config_manager.CONFIG_PATH", cfg_file)

    parent = QWidget()
    dlg = ForgeSettingsDialog(parent)

    try:
        assert dlg.windowTitle() == "Настройки SD WebUI Forge — NeonRoute"
        assert dlg.line_path is not None
        assert dlg.lbl_launcher_status is not None
        assert dlg.lbl_patch_status is not None
        assert dlg.switch_fp8 is not None
        assert dlg.segmented_art is not None
        assert dlg.btn_save is not None
        assert dlg.btn_cancel is not None

        # Verify initial values from config
        assert dlg.switch_fp8.isChecked() is True
        assert dlg.segmented_art.currentRouteKey() == "manual"

        # Test patch checking button slot
        dlg._on_check_patch_clicked()

        # Test changing values and saving
        dlg.switch_fp8.setChecked(False)
        dlg.segmented_art.setCurrentItem("auto")
        dlg._on_save_clicked()

        # Verify config saved correctly
        saved = ConfigManager.load_config()
        assert saved["forge_sdxl_fp8_enabled"] is False
        assert saved["auto_generate_art"] is True

        # Test selecting a new valid Forge path
        fake_forge = tmp_path / "fake_forge"
        fake_forge.mkdir()
        (fake_forge / "forge_launcher.bat").write_text("@echo off\n", encoding="utf-8")
        dlg.line_path.setText(str(fake_forge))
        assert "forge_launcher.bat" in dlg.lbl_launcher_status.text()
    finally:
        dlg.close()
        dlg.deleteLater()


STOCK_API_PY = '''
class Api:
    def __init__(self):
        self.add_api_route("/sdapi/v1/unload-checkpoint", self.unloadapi, methods=["POST"])
        self.add_api_route("/sdapi/v1/reload-checkpoint", self.reloadapi, methods=["POST"])

    def unloadapi(self):
        sd_models.unload_model_weights()
        return {}

    def reloadapi(self):
        sd_models.send_model_to_device(shared.sd_model)
        return {}

    def set_config(self, req: dict[str, Any]):
        for k, v in req.items():
            shared.opts.set(k, v, is_api=True)
        shared.opts.save(shared.config_filename)

        return

    def get_sd_models(self):
        return [{"title": x.title, "model_name": x.model_name, "hash": x.shorthash, "sha256": x.sha256, "filename": x.filename} for x in sd_models.checkpoints_list.values()]
'''

STOCK_MODELS_PY = '''
class SDModelItem(BaseModel):
    title: str = Field(title="Title")
    model_name: str = Field(title="Model name")
    hash: Optional[str] = Field(title="Short hash")
    sha256: Optional[str] = Field(title="sha256 hash")
    filename: str = Field(title="Filename")
    config: Optional[str] = Field(title="Config file")
'''

STOCK_SD_MODELS_PY = '''
def reload_model_weights(sd_model=None, info=None, forced_reload=False):
    pass

def unload_model_weights(sd_model=None, info=None):
    pass

def apply_token_merging(sd_model, token_merging_ratio):
    pass
'''


def test_forge_settings_dialog_patch_lifecycle(qapp, tmp_path, monkeypatch):
    """Verifies dialog dynamically reacts to patch status and handles apply/restore actions."""
    forge_dir = tmp_path / "mock_forge_ui"
    webui_dir = forge_dir / "webui"
    (webui_dir / "modules" / "api").mkdir(parents=True, exist_ok=True)
    (webui_dir / "modules" / "api" / "api.py").write_text(STOCK_API_PY, encoding="utf-8")
    (webui_dir / "modules" / "api" / "models.py").write_text(STOCK_MODELS_PY, encoding="utf-8")
    (webui_dir / "modules" / "sd_models.py").write_text(STOCK_SD_MODELS_PY, encoding="utf-8")
    (forge_dir / "forge_launcher.bat").write_text("@echo off\n", encoding="utf-8")

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("utils.config_manager.CONFIG_PATH", cfg_file)

    parent = QWidget()
    dlg = ForgeSettingsDialog(parent)

    try:
        dlg.line_path.setText(str(forge_dir))
        # Status should indicate patch is needed and button offers to apply
        assert "Требуется патч" in dlg.lbl_patch_status.text()
        assert dlg.btn_check_patch.text() == "Применить патч"

        # Apply patch via button click
        dlg._on_check_patch_clicked()
        assert "активен" in dlg.lbl_patch_status.text()
        assert dlg.btn_check_patch.text() == "Откатить патч"

        # Rollback patch via button click
        dlg._on_check_patch_clicked()
        assert "Требуется патч" in dlg.lbl_patch_status.text()
        assert dlg.btn_check_patch.text() == "Применить патч"
    finally:
        dlg.close()
        dlg.deleteLater()

