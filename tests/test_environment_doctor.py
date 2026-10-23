import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication

from services.environment_doctor import (
    DEPENDENCY_CATALOG,
    EnvironmentDoctor,
    PipInstallWorker
)
from services.engineer_prompts import get_engineer_system_prompt
from utils.config_manager import ConfigManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


def test_dependency_catalog_structure():
    assert "git" in DEPENDENCY_CATALOG
    assert "pygame" in DEPENDENCY_CATALOG
    assert "customtkinter" in DEPENDENCY_CATALOG
    assert "openpyxl" in DEPENDENCY_CATALOG
    assert "docx" in DEPENDENCY_CATALOG

    for key, meta in DEPENDENCY_CATALOG.items():
        assert "type" in meta
        assert "name" in meta
        assert "description" in meta
        assert "consequence" in meta
        assert "is_critical" in meta


def test_environment_doctor_check_missing_mocked():
    with patch("importlib.util.find_spec") as mock_find_spec, \
         patch("shutil.which") as mock_which, \
         patch("pathlib.Path.is_file") as mock_is_file:
        
        # When all found
        mock_find_spec.return_value = MagicMock()
        mock_which.return_value = "git"
        mock_is_file.return_value = False
        missing = EnvironmentDoctor.check_missing()
        assert len(missing) == 0

        # When git missing
        mock_which.return_value = None
        missing = EnvironmentDoctor.check_missing()
        assert any(item["key"] == "git" for item in missing)

        # When openpyxl missing
        mock_find_spec.side_effect = lambda k: None if k == "openpyxl" else MagicMock()
        missing = EnvironmentDoctor.check_missing()
        assert any(item["key"] == "openpyxl" for item in missing)


def test_pip_install_worker_success(qapp):
    worker = PipInstallWorker(["pygame"])
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("Successfully installed", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        progress_msgs = []
        finished_results = []
        worker.progress.connect(progress_msgs.append)
        worker.finished.connect(lambda ok, msg: finished_results.append((ok, msg)))

        worker.run()
        assert len(progress_msgs) == 1
        assert "pygame" in progress_msgs[0]
        assert len(finished_results) == 1
        assert finished_results[0][0] is True


def test_pip_install_worker_failure(qapp):
    worker = PipInstallWorker(["fake_lib"])
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("No matching distribution", "")
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc

        finished_results = []
        worker.finished.connect(lambda ok, msg: finished_results.append((ok, msg)))

        worker.run()
        assert len(finished_results) == 1
        assert finished_results[0][0] is False
        assert "Ошибка установки fake_lib" in finished_results[0][1]


def test_dependency_dialog_ui_and_actions(qapp, tmp_path):
    from ui.dialogs.dependency_dialog import DependencyDialog

    with patch.object(EnvironmentDoctor, "check_missing") as mock_check:
        mock_check.return_value = [
            DEPENDENCY_CATALOG["pygame"],
            DEPENDENCY_CATALOG["git"],
        ]
        dialog = DependencyDialog()
        assert "pygame" in dialog.checkboxes
        assert dialog.checkboxes["pygame"].isChecked() is True
        # Binary checkbox shouldn't be in install checkboxes
        assert "git" not in dialog.checkboxes

        # Test Skip with "don't show again"
        test_cfg = tmp_path / "config.json"
        with patch.object(ConfigManager, "load_config", return_value={"check_dependencies_on_startup": True}), \
             patch.object(ConfigManager, "save_config") as mock_save, \
             patch("PyQt6.QtWidgets.QMessageBox.information") as mock_info:
            dialog.cb_dont_show.setChecked(True)
            dialog._on_skip()
            mock_save.assert_called_once()
            saved = mock_save.call_args[0][0]
            assert saved["check_dependencies_on_startup"] is False
            mock_info.assert_called_once()


def test_excel_directives_in_engineer_prompts():
    prompt = get_engineer_system_prompt(artifact_format="excel")
    assert "=SUM" in prompt
    assert "=AVERAGE" in prompt
    assert "=COUNT" in prompt
    assert "=VLOOKUP" in prompt
    assert "СТРОГО НА АНГЛИЙСКОМ ЯЗЫКЕ ЗАГЛАВНЫМИ БУКВАМИ" in prompt
    assert "bold=True" in prompt
    assert "Border" in prompt
    assert "нативные формулы Excel" in prompt


def test_main_window_menu_and_dialog_integration(qapp):
    from ui.main_window import MainWindow

    # Check MainWindow has open_dependency_dialog method
    assert hasattr(MainWindow, "open_dependency_dialog")


def test_menu_builder_help_actions(qapp):
    from PyQt6.QtWidgets import QMainWindow
    from ui.builders.menu_builder import MenuBuilder

    win = QMainWindow()
    win.app_config = {}
    win.open_dependency_dialog = MagicMock()
    win._open_help_dialog = MagicMock()
    win._open_about_dialog = MagicMock()
    MenuBuilder.setup_menus(win)

    help_menu = None
    for action in win.menuBar().actions():
        if action.text().replace("&", "") == "Справка":
            help_menu = action.menu()
            break
    assert help_menu is not None
    action_texts = [a.text() for a in help_menu.actions()]
    assert any("Проверка компонентов" in t for t in action_texts)
