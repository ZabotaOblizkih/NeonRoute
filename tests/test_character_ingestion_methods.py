"""
Тесты физических способов импорта карточек в CharactersGalleryDialog:
1. Прямое копирование .json в папку (через Проводник).
2. Импорт внешнего .json через системный диалог (_import_character).
3. Скачивание карточки через каталог Chub.ai (_on_chub_card_downloaded).
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QWidget
from services.tavern_parser import CharacterCard
from services.task_queue import InferenceTask
from ui.dialogs.characters_gallery_dialog import CharactersGalleryDialog

try:
    from ui.controllers.roleplay_flow import RoleplayFlowControllerMixin
except ImportError:
    try:
        from ui.roleplay_flow import RoleplayFlowControllerMixin
    except ImportError:
        try:
            from controllers.roleplay_flow import RoleplayFlowControllerMixin
        except ImportError:
            from ui.main_window import RoleplayFlowControllerMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class HostMainWindowIngestionMock(QWidget, RoleplayFlowControllerMixin):
    """Эмулятор главного окна для приема карточки после любого способа импорта."""
    def __init__(self):
        super().__init__()
        self.current_session = None
        self.active_character = None
        self.chat_history = []
        self._current_user_request = ""
        self._active_tasks = {}
        self.selected_forge_checkpoint = None

        self.lm_combo = MagicMock()
        self.lm_combo.currentText.return_value = "default"

        self.lm_client = MagicMock()
        self.task_queue = MagicMock()
        self.pill_forge = MagicMock()
        self.pill_forge.is_online = False
        self.app_config = {"auto_generate_art": False}

    def start_roleplay_session(self, card: CharacterCard):
        self.active_character = card
        
        pool = card.get_greetings_pool() if hasattr(card, "get_greetings_pool") else []
        idx = getattr(card, "selected_greeting_idx", None)
        if idx is not None and pool and 0 <= idx < len(pool):
            first_greeting = pool[idx].get("effective") or pool[idx].get("orig") or getattr(card, "first_mes", "")
        else:
            first_greeting = getattr(card, "first_mes_ru", None) or getattr(card, "first_mes", "") or "Привет!"

        self.current_session = {
            "id": "sess_ingest_test",
            "character_id": getattr(card, "id", "char_test"),
            "character_name": card.name,
            "user_persona_id": None,
            "messages": [
                {"role": "assistant", "content": first_greeting, "is_user": False}
            ]
        }
        self.chat_history = list(self.current_session["messages"])


@pytest.fixture
def ingestion_env(qapp, tmp_path, monkeypatch):
    """Изолированная среда с чистыми папками персонажей и кэша."""
    chars_dir = tmp_path / "characters"
    thumbs_dir = tmp_path / "cache" / "gallery_thumbs"
    chars_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.CHARACTERS_DIR", chars_dir)
    monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.GALLERY_THUMBS_DIR", thumbs_dir)

    host = HostMainWindowIngestionMock()
    dlg = CharactersGalleryDialog(parent=host)
    dlg.start_session_requested.connect(host.start_roleplay_session)

    yield dlg, host, chars_dir

    dlg._cleanup_dialog_tasks()
    dlg.close()
    host.deleteLater()


class TestCharacterIngestionPipeline:
    """Проверка всех путей импорта файлов карточек."""

    def test_direct_file_drop_into_characters_folder(self, ingestion_env):
        """Сценарий 1: Пользователь сам закинул .json в папку data/characters/."""
        dlg, host, chars_dir = ingestion_env

        card_file = chars_dir / "direct_drop_hero.json"
        card_data = {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": "Лира",
                "description": "Лесная лучница с серебряными волосами.",
                "personality": "Меткая и молчаливая",
                "first_mes": "*Лира целится из лука в чащу леса.*"
            }
        }
        with open(card_file, "w", encoding="utf-8") as f:
            json.dump(card_data, f, ensure_ascii=False)

        dlg._load_characters()

        assert dlg.list_widget.count() == 1
        assert "Лира" in dlg.list_widget.item(0).text()
        assert dlg.active_entry is not None
        assert dlg.active_entry["card"].name == "Лира"
        assert dlg.input_name.text() == "Лира"

        dlg._start_dialog()
        assert host.active_character is not None
        assert host.active_character.name == "Лира"
        assert "*Лира целится из лука" in host.chat_history[0]["content"]

    def test_import_external_json_via_file_dialog(self, ingestion_env, tmp_path):
        """Сценарий 2: Пользователь нажал «Импорт .json» и выбрал файл через системный диалог."""
        dlg, host, chars_dir = ingestion_env

        external_dir = tmp_path / "downloads"
        external_dir.mkdir(parents=True, exist_ok=True)
        src_json = external_dir / "external_char.json"
        with open(src_json, "w", encoding="utf-8") as f:
            json.dump({
                "name": "Рейвен",
                "description": "Кибернетический наемник.",
                "first_mes": "Системы активированы."
            }, f, ensure_ascii=False)

        with patch("PyQt6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(src_json), "JSON (*.json)")), \
             patch("PyQt6.QtWidgets.QMessageBox.information"):
            dlg._import_character()

        target_files = list(chars_dir.glob("*.json"))
        assert len(target_files) >= 1
        assert any("Рейвен" in p.name for p in target_files)

        assert dlg.active_entry["card"].name == "Рейвен"
        assert dlg.input_desc.toPlainText() == "Кибернетический наемник."

    def test_import_from_chub_download_callback(self, ingestion_env, tmp_path):
        """Сценарий 3: Скачивание карточки через встроенный каталог Chub.ai."""
        dlg, host, chars_dir = ingestion_env

        downloaded_path = chars_dir / "chub_downloaded_hero.json"
        with open(downloaded_path, "w", encoding="utf-8") as f:
            json.dump({
                "spec": "chara_card_v2",
                "data": {
                    "name": "Аврора",
                    "description": "Жрица солнца.",
                    "personality": "Добрая и мудрая",
                    "first_mes": "Да пребудет с тобой свет."
                }
            }, f, ensure_ascii=False)

        dlg._on_chub_card_downloaded(downloaded_path)

        assert dlg.active_entry is not None
        assert dlg.active_entry["card"].name == "Аврора"
        assert dlg.input_name.text() == "Аврора"

        dlg._start_dialog()
        assert host.active_character.name == "Аврора"

        with patch("services.session_manager.SessionManager.get_active_user_persona", return_value={"name": "Роман", "gender": "male"}), \
             patch("services.rp_cast_manager.RPCastManager.scan_and_tick", return_value={}), \
             patch("services.rp_cast_manager.RPCastManager.get_world_state", return_value=[]):

            host.chat_history.append({"role": "user", "content": "Привет, Аврора!"})
            host.current_session["messages"].append({"role": "user", "content": "Привет, Аврора!"})
            host._enqueue_roleplay_llm_task()

        llm_tasks = [
            call[0][0] for call in host.task_queue.enqueue.call_args_list
            if isinstance(call[0][0], InferenceTask) and call[0][0].task_type == "llm"
        ]
        assert len(llm_tasks) == 1
        task: InferenceTask = llm_tasks[0]
        sys_prompt = task.kwargs["messages"][0]["content"]

        assert "Аврора" in sys_prompt
        assert "Жрица солнца" in sys_prompt