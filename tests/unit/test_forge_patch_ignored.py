"""
tests/unit/test_forge_patch_ignored.py
======================================
Тесты для проверки логики 'Больше не предлагать для этой папки' (forge_patch_ignored_paths):
1. Корректное распознавание путей независимо от регистра диска на Windows
2. Подавление повторного показа _check_and_prompt_forge_patch при наличии пути в ignored
3. Сохранение флага при нажатии 'Больше не предлагать'
4. Защита от сброса флага игнорирования при открытии обзора в ForgeSettingsDialog
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow
from services.forge_patcher import ForgePatchStatus, ForgePatcher
from utils.config_manager import ConfigManager
from ui.controllers.hardware_engine import HardwareEngineMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class DummyHardwareHost(HardwareEngineMixin, QMainWindow):
    """Легковесный хост для тестирования HardwareEngineMixin."""
    def __init__(self):
        super().__init__()
        self.app_config = {}


def test_check_and_prompt_forge_patch_suppressed_by_ignored_paths(qapp, tmp_path, monkeypatch):
    """Проверяет, что при наличии пути в forge_patch_ignored_paths окно не всплывает."""
    dummy_dir = tmp_path / "dummy_forge"
    dummy_dir.mkdir()

    # Задаем путь в конфиге в одном регистре, а проверяем в другом (эмуляция Windows c: vs C:)
    lower_path = str(dummy_dir).lower()
    upper_path = str(dummy_dir).upper()

    monkeypatch.setattr(
        ConfigManager,
        "load_config",
        lambda: {"forge_patch_ignored_paths": [lower_path]}
    )
    monkeypatch.setattr(
        ForgePatcher,
        "check_status",
        lambda root: (ForgePatchStatus.NEEDS_PATCH, "Требуется патч")
    )

    prompt_shown = []
    monkeypatch.setattr(
        "qfluentwidgets.MessageBoxBase.exec",
        lambda self: prompt_shown.append("shown") or False
    )

    host = DummyHardwareHost()
    res = host._check_and_prompt_forge_patch(upper_path)

    assert res is False
    assert len(prompt_shown) == 0, "Диалог предложения патча не должен отображаться для игнорируемого пути"
    host.deleteLater()


def test_forge_settings_dialog_browse_does_not_wipe_ignored_path(qapp, tmp_path, monkeypatch):
    """Проверяет, что выбор папки через Обзор в ForgeSettingsDialog не стирает ее из forge_patch_ignored_paths."""
    dummy_dir = tmp_path / "my_forge"
    dummy_dir.mkdir()

    cfg_store = {
        "forge_patch_ignored_paths": [str(dummy_dir.resolve())],
        "forge_sdxl_fp8_enabled": True,
        "auto_generate_art": False,
    }

    monkeypatch.setattr(ConfigManager, "load_config", lambda: cfg_store.copy())
    monkeypatch.setattr(ConfigManager, "save_config", lambda c: cfg_store.update(c))
    monkeypatch.setattr("config.FORGE_ROOT_DIR", str(dummy_dir))
    monkeypatch.setattr(
        ForgePatcher,
        "check_status",
        lambda root: (ForgePatchStatus.NEEDS_PATCH, "Требуется патч")
    )
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(dummy_dir)
    )

    from ui.dialogs.forge_settings_dialog import ForgeSettingsDialog

    dlg = ForgeSettingsDialog(parent=None)
    dlg.show()
    qapp.processEvents()

    # Вызываем выбор папки через обзор
    dlg._on_browse_clicked()

    # Проверяем, что путь остался в forge_patch_ignored_paths!
    saved_ignored = cfg_store.get("forge_patch_ignored_paths", [])
    norm_dummy = os.path.normcase(os.path.abspath(str(dummy_dir)))
    assert any(os.path.normcase(os.path.abspath(p)) == norm_dummy for p in saved_ignored), \
        "Флаг игнорирования не должен стираться при выборе пути в настройках"

    dlg.deleteLater()
