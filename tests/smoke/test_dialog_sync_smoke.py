"""
Smoke test verifying that window synchronization is triggered when
ModelsManagerDialog is closed via reject() (close button 'X' or Escape key).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication, QWidget, QDialog

import config
from services.art_director_config import save_art_director_models
from services.art_director.director import ArtDirector
from services.model_router import ModelRouter
from ui.controllers.hardware_engine import HardwareEngineMixin
from ui.models_manager_dialog import ModelsManagerDialog


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class DummyWindow(QWidget, HardwareEngineMixin):
    """Headless window simulating UI synchronization with HardwareEngineMixin."""

    def __init__(self, art_director, forge_client):
        super().__init__()
        self.art_director = art_director
        self.forge_client = forge_client
        self.available_forge_checkpoints = []
        self.current_session = None
        self.current_mode = "roleplay"
        self.selected_forge_checkpoint = None

    def statusBar(self):
        return MagicMock()

    def _cancel_lms_reload(self):
        pass

    def _reload_active_lms_model_async(self, delay=1.2):
        pass

    def _refresh_forge_models(self):
        self._on_forge_models_discovered([])

    def get_effective_mode(self):
        return "roleplay"


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """
    Strict isolation fixture:
    Redirects all model configuration paths to an isolated tmp_path / 'art_director_models.json'.
    """
    tmp_json_path = tmp_path / "art_director_models.json"

    ModelRouter._instance = None

    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", tmp_json_path)
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", tmp_json_path)

    import ui.models_manager_dialog as mmd
    if hasattr(mmd, "ART_DIRECTOR_MODELS_PATH"):
        monkeypatch.setattr(mmd, "ART_DIRECTOR_MODELS_PATH", tmp_json_path)

    yield tmp_json_path

    ModelRouter._instance = None


def test_models_manager_close_via_reject_sync(qapp, isolated_storage):
    """
    Verifies that ModelsManagerDialog synchronization runs unconditionally
    when closed via reject() (cross button / Esc) through window._open_models_manager_dialog().
    """
    tmp_json_path = isolated_storage

    toggled_model_name = "Dragon Slayer 1.5"
    initial_models = [
        {
            "id": "realistic",
            "name": "Realistic Vision",
            "checkpoint": "realisticVisionV60B1_v51HyperVAE_pruned_fp16.safetensors",
            "active": True,
            "pipeline": {
                "steps": 25,
                "cfg_scale": 6.0,
                "enable_adetailer": True,
                "negative_prompt": "ugly, blurry"
            }
        },
        {
            "id": "inactive_dragon",
            "name": toggled_model_name,
            "checkpoint": "dragon_model.safetensors",
            "active": False,
            "pipeline": {
                "steps": 27,
                "cfg_scale": 6.5,
                "enable_adetailer": True,
                "negative_prompt": "badhand4, zombie"
            }
        }
    ]

    # Initialize disk storage with initial model list
    save_art_director_models(initial_models)

    art_director = ArtDirector()
    mock_forge = MagicMock()
    mock_forge.get_models.return_value = []

    # 1. Create test window and verify initial checkpoint list
    window = DummyWindow(art_director=art_director, forge_client=mock_forge)
    window._on_forge_models_discovered([])

    assert toggled_model_name not in window.available_forge_checkpoints, (
        f"Precondition failed: '{toggled_model_name}' should be inactive initially"
    )

    # 2. Mock ModelsManagerDialog.exec to simulate user toggling the model and closing via reject()
    def fake_exec(dialog_self):
        # 1) Toggle activity of test model to True and persist to disk
        target_row = next(i for i, r in enumerate(dialog_self.row_widgets) if r["name"] == toggled_model_name)
        dialog_self.row_widgets[target_row]["chk"].setChecked(True)
        dialog_self._save_models_to_json()
        dialog_self.reject()
        # 2) Return QDialog.DialogCode.Rejected (0)
        return int(QDialog.DialogCode.Rejected)

    # 3. Open dialog through controller lifecycle method
    with patch.object(ModelsManagerDialog, "_start_forge_scan"), \
         patch.object(ModelsManagerDialog, "exec", fake_exec):
        window._open_models_manager_dialog()

    # 4. Verify that active: True was persisted to JSON on disk
    raw_disk_data = json.loads(tmp_json_path.read_text(encoding="utf-8-sig"))
    saved_entry = next(m for m in raw_disk_data.get("models", []) if m.get("name") == toggled_model_name)
    assert saved_entry.get("active") is True, f"Model '{toggled_model_name}' not saved as active=True in JSON"

    # 5. Verify that window received the update despite dialog being closed via reject()
    assert toggled_model_name in window.available_forge_checkpoints, (
        f"Рассинхрон UI: модель '{toggled_model_name}' сохранена в JSON как active=True, "
        f"но отсутствует в available_forge_checkpoints окна из-за закрытия через reject()."
    )
