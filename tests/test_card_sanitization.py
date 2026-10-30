"""
Unit and integration tests for Chub.ai and Tavern V2 character card sanitization.
Verifies removal of promotional links, other bot lists, and author meta-comments,
while preserving legitimate character descriptions and moving junk to creator_notes.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QWidget

from services.chub_client import sanitize_card_description, ChubClient
from services.tavern_parser import CharacterCard, TavernParser
from ui.dialogs.characters_gallery_dialog import CharactersGalleryDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class TestSanitizeCardDescription:
    def test_pure_junk_card_description(self):
        """Карточка, состоящая исключительно из промо-мусора, должна стать абсолютно пустой."""
        raw = (
            "Card made with canon accuracy in mind.\n"
            "More Nier Automata cards:\n"
            "9S - https://www.chub.ai/characters/cptpants/yorha-9s\n"
            "A2 - https://www.chub.ai/characters/cptpants/yorha-a2-aea1bde0"
        )
        clean_text, notes = sanitize_card_description(raw)
        assert clean_text == ""
        assert len(notes) == 4
        assert "Card made with canon accuracy in mind." in notes
        assert "https://www.chub.ai/characters/cptpants/yorha-9s" in notes[2]
        assert "https://www.chub.ai/characters/cptpants/yorha-a2-aea1bde0" in notes[3]

    def test_narrative_with_promotional_footer(self):
        """Повествовательное описание сохраняется, промо-ссылки удаляются."""
        raw = (
            "YoRHa No.2 Type B is an all-purpose battle android deployed by YoRHa.\n"
            "She possesses a quiet, stoic personality and follows orders diligently.\n\n"
            "Support me on Patreon: https://patreon.com/cptpants\n"
            "Join our Discord: discord.gg/example\n"
            "Leave a review and star!"
        )
        clean_text, notes = sanitize_card_description(raw)
        assert "YoRHa No.2 Type B is an all-purpose battle android" in clean_text
        assert "She possesses a quiet, stoic personality" in clean_text
        assert "patreon.com" not in clean_text
        assert "discord.gg" not in clean_text
        assert "Leave a review" not in clean_text
        assert any("patreon.com" in n for n in notes)
        assert any("discord.gg" in n for n in notes)
        assert any("Leave a review" in n for n in notes)

    def test_cyrillic_promotional_headers(self):
        """Русскоязычные промо-блоки успешно распознаются и очищаются."""
        raw = (
            "Древний маг, хранитель библиотеки артефактов.\n"
            "Другие персонажи:\n"
            "- Элеонора: https://chub.ai/characters/test/eleonora\n"
            "Поддержать автора: https://boosty.to/author\n"
            "Автор карточки: ВеликийМаг"
        )
        clean_text, notes = sanitize_card_description(raw)
        assert clean_text == "Древний маг, хранитель библиотеки артефактов."
        assert any("Другие персонажи:" in n for n in notes)
        assert any("boosty.to" in n for n in notes)
        assert any("Автор карточки" in n for n in notes)

    def test_bullet_points_inside_character_lore_preserved(self):
        """Обычные списки в описании персонажа (не промо) должны оставаться нетронутыми."""
        raw = (
            "Combat specifications:\n"
            "- Dual blade mastery\n"
            "- High mobility thrusters\n"
            "- Pod 042 support system"
        )
        clean_text, notes = sanitize_card_description(raw)
        assert clean_text == raw
        assert len(notes) == 0


class TestChubClientDownloadSanitization:
    def test_download_card_sanitizes_api_payload(self, tmp_path, monkeypatch):
        """При загрузке через API описание очищается от ссылок, а ссылки попадают в creator_notes."""
        monkeypatch.setattr("services.chub_client.CHARACTERS_DIR", tmp_path)

        client = ChubClient()
        fake_api_response = {
            "node": {
                "name": "2B",
                "tagline": "Battle android",
                "description": "More Nier Automata cards: https://chub.ai/yorha-9s",
                "definition": {
                    "description": "2B is an all-purpose battle android.\nCheck out my other bots:\nhttps://chub.ai/a2",
                    "personality": "Quiet and stoic.\nCreated by cptpants",
                    "first_message": "Everything that lives is designed to end.",
                    "scenario": "Ruin City exploration.",
                    "example_dialogs": "<START>\n{{char}}: Mission acknowledged."
                }
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(fake_api_response)
        mock_resp.json.return_value = fake_api_response

        with patch.object(client.session, "get", return_value=mock_resp):
            out_file = client.download_card("cptpants/yorha-2b", "2B")

        assert out_file is not None
        assert out_file.exists()
        with open(out_file, "r", encoding="utf-8") as f:
            data = json.load(f)["data"]

        assert data["name"] == "2B"
        assert data["description"] == "2B is an all-purpose battle android."
        assert "chub.ai" not in data["description"]
        assert data["personality"] == "Quiet and stoic."
        assert "Created by" not in data["personality"]
        assert "Battle android" in data["creator_notes"]
        assert "https://chub.ai/a2" in data["creator_notes"]
        assert "Created by cptpants" in data["creator_notes"]


class TestGalleryImportSanitization:
    def test_import_json_card_cleans_description_and_personality(self, qapp, tmp_path, monkeypatch):
        """Импорт JSON файла очищает описание и переносит метаданные в creator_notes."""
        chars_dir = tmp_path / "characters"
        chars_dir.mkdir(parents=True, exist_ok=True)
        thumbs_dir = tmp_path / "cache" / "gallery_thumbs"
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.CHARACTERS_DIR", chars_dir)
        monkeypatch.setattr("services.tavern_parser.CHARACTERS_DIR", chars_dir)

        parent_widget = QWidget()
        dlg = CharactersGalleryDialog(parent=parent_widget)

        raw_card = {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": "A2",
                "description": (
                    "YoRHa Type A No.2 is a prototype battle android.\n\n"
                    "More Nier Automata cards:\n"
                    "2B - https://chub.ai/2b\n"
                    "9S - https://chub.ai/9s"
                ),
                "personality": "Rebellious.\nCard made with canon accuracy in mind.",
                "first_mes": "I never had any interest in joining your little organization.",
                "creator_notes": "Original character notes."
            }
        }

        src_json = tmp_path / "incoming_a2.json"
        with open(src_json, "w", encoding="utf-8") as f:
            json.dump(raw_card, f)

        with patch("PyQt6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(src_json), "JSON (*.json)")), \
             patch("PyQt6.QtWidgets.QMessageBox.information"):
            dlg._import_character()

        # Проверяем импортированный файл в chars_dir
        imported_files = list(chars_dir.glob("*.json"))
        assert len(imported_files) >= 1
        imported_json = imported_files[0]

        with open(imported_json, "r", encoding="utf-8") as f:
            imported_data = json.load(f)["data"]

        assert imported_data["description"] == "YoRHa Type A No.2 is a prototype battle android."
        assert "chub.ai" not in imported_data["description"]
        assert imported_data["personality"] == "Rebellious."
        assert "Card made with" not in imported_data["personality"]
        assert "Original character notes." in imported_data["creator_notes"]
        assert "https://chub.ai/2b" in imported_data["creator_notes"]
        assert "Card made with canon accuracy in mind." in imported_data["creator_notes"]

        dlg._cleanup_dialog_tasks()
        dlg.close()
        parent_widget.deleteLater()
