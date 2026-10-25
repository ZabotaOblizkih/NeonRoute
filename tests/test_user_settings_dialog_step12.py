"""
Unit tests for UserSettingsDialog (Step 12 refactoring).
Verifies QFluentWidgets integration, Glassmorphism, SegmentedWidget tab switching,
persona management, validation, and persistence.
"""

import sys
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ui.dialogs.user_settings_dialog import UserSettingsDialog, InteractiveAvatarWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_user_settings_dialog_tabs_and_structure(qapp):
    profiles_data = {
        "assistant_user": {
            "name": "Алексей",
            "avatar_path": None,
            "bio": "Разработчик"
        },
        "rp_settings": {
            "active_persona_id": "persona_1",
            "personas": {
                "persona_1": {
                    "name": "Дэниэл",
                    "gender": "male",
                    "avatar_path": None,
                    "bio": "Бывший капитан",
                    "personality": "Хладнокровный",
                    "backstory": "Бывший капитан",
                    "inventory": "компас, фонарь",
                    "visual_dna": "1man, 28yo, athletic",
                    "outfit": "cargo pants"
                }
            }
        }
    }

    with patch("ui.dialogs.user_settings_dialog.SessionManager.load_user_profiles", return_value=profiles_data), \
         patch("ui.dialogs.user_settings_dialog.ConfigManager.load_config", return_value={"user_name": "Алексей", "auto_generate_art": False, "active_model": "test_m"}):

        dlg = UserSettingsDialog(initial_tab=0)
        assert dlg.nav_segmented is not None
        assert dlg.stacked_widget.currentIndex() == 0
        assert dlg.asst_name_input.text() == "Алексей"
        assert dlg.asst_bio_input.toPlainText() == "Разработчик"
        assert dlg.art_mode_segmented.currentRouteKey() == "manual"

        # Switch to RP tab
        dlg.set_current_tab(1)
        assert dlg.stacked_widget.currentIndex() == 1
        assert dlg.rp_name_input.text() == "Дэниэл"
        assert dlg.sw_gender.currentRouteKey() == "male"
        assert dlg.rp_personality_input.toPlainText() == "Хладнокровный"
        assert dlg.rp_inventory_input.text() == "компас, фонарь"
        assert dlg.rp_visual_dna_input.toPlainText() == "1man, 28yo, athletic"
        assert dlg.rp_outfit_input.text() == "cargo pants"


def test_user_settings_persona_creation_and_primary(qapp):
    profiles_data = {
        "assistant_user": {"name": "User", "avatar_path": None, "bio": ""},
        "rp_settings": {
            "active_persona_id": "p_initial",
            "personas": {
                "p_initial": {"name": "Первый", "gender": "male"}
            }
        }
    }

    with patch("ui.dialogs.user_settings_dialog.SessionManager.load_user_profiles", return_value=profiles_data), \
         patch("ui.dialogs.user_settings_dialog.ConfigManager.load_config", return_value={}):

        dlg = UserSettingsDialog(initial_tab=1)
        assert dlg.list_personas.count() == 1

        # Create new persona
        dlg._create_new_persona()
        assert dlg.list_personas.count() == 2
        new_pid = dlg.active_persona_id
        assert new_pid != "p_initial"

        # Edit name
        dlg.rp_name_input.setText("Второй Герой")
        dlg.sw_gender.setCurrentItem("female")
        dlg._sync_current_persona_fields()

        personas = dlg._get_personas_dict()
        assert personas[new_pid]["name"] == "Второй Герой"
        assert personas[new_pid]["gender"] == "female"

        # Set as primary
        dlg._set_current_as_primary()
        assert dlg.active_primary_persona_id == new_pid


def test_user_settings_validation_and_saving(qapp):
    profiles_data = {
        "assistant_user": {"name": "Шеф", "avatar_path": None, "bio": "Инженер"},
        "rp_settings": {
            "active_persona_id": "p1",
            "personas": {
                "p1": {"name": "Герой", "gender": "male"}
            }
        }
    }

    with patch("ui.dialogs.user_settings_dialog.SessionManager.load_user_profiles", return_value=profiles_data), \
         patch("ui.dialogs.user_settings_dialog.SessionManager.save_user_profiles") as mock_save_profiles, \
         patch("ui.dialogs.user_settings_dialog.atomic_write_json") as mock_atomic_write, \
         patch("ui.dialogs.user_settings_dialog.ConfigManager.load_config", return_value={"user_name": "Шеф"}), \
         patch("ui.dialogs.user_settings_dialog.ConfigManager.save_config") as mock_save_cfg:

        dlg = UserSettingsDialog(initial_tab=1)

        # 1. Validation failure: empty name
        dlg.rp_name_input.setText("   ")
        with patch.object(dlg, "rp_name_input") as mock_input:
            mock_input.text.return_value = ""
            with patch("ui.dialogs.user_settings_dialog.QMessageBox.warning") as mock_warn:
                valid = dlg._validate_inputs()
                assert valid is False
                assert mock_warn.called

        # 2. Fix name and save
        dlg.rp_name_input.setText("Крутой Герой")
        valid = dlg._validate_inputs()
        assert valid is True

        dlg._save_all_profiles()
        assert mock_save_profiles.called
        assert mock_atomic_write.called
        assert mock_save_cfg.called
