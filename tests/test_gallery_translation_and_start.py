"""
Unit and Integration tests for Character Gallery Translation and Start Flow.
Verifies:
1. Race-free closing of CharactersGalleryDialog and deferred session creation.
2. Resilient translation delimiter parsing (case-insensitive, spaces, markdown artifacts).
3. Protection of _ru localization fields from being overwritten.
4. Correct single-block Russian greeting rendering in Roleplay chat without duplicate blocks or re-translations.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QDialog, QWidget
from PyQt6.QtCore import QTimer

from services.tavern_parser import TavernParser, CharacterCard
from services.sandbox_manager import SandboxManager
from ui.dialogs.characters_gallery_dialog import CharactersGalleryDialog
from ui.controllers.roleplay_controller import RoleplayControllerMixin
from ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def gallery_env(qapp, tmp_path, monkeypatch):
    chars_dir = tmp_path / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir = tmp_path / "cache" / "gallery_thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.CHARACTERS_DIR", chars_dir)
    monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.GALLERY_THUMBS_DIR", thumbs_dir)
    monkeypatch.setattr("services.tavern_parser.CHARACTERS_DIR", chars_dir)

    parent_widget = QWidget()
    parent_widget.task_queue = MagicMock()
    parent_widget.lm_client = MagicMock()
    parent_widget.current_session = None

    dlg = CharactersGalleryDialog(parent=parent_widget)
    yield dlg, parent_widget, chars_dir
    dlg._cleanup_dialog_tasks()
    dlg.close()
    parent_widget.deleteLater()


class TestGalleryCloseAndStartRace:
    """Проверка закрытия диалога и старта сессии без гонки 'окно в окне'."""

    def test_start_dialog_sets_selected_card_and_accepts(self, gallery_env):
        dlg, _, chars_dir = gallery_env
        card = CharacterCard(
            name="Элеонора",
            description="Кибернетическая мечница.",
            first_mes="Клинок готов к бою."
        )
        card_path = chars_dir / "eleonora.json"
        with open(card_path, "w", encoding="utf-8") as f:
            json.dump({"name": "Элеонора", "description": card.description, "first_mes": card.first_mes}, f)

        dlg.active_entry = {"card": card, "path": card_path}
        dlg.input_name.setText(card.name)
        dlg.input_desc.setPlainText(card.description)
        dlg.input_first_mes.setPlainText(card.first_mes)

        with patch.object(dlg, "accept") as mock_accept:
            dlg._start_dialog()
            assert dlg.selected_card == card
            mock_accept.assert_called_once()

    def test_roleplay_controller_creates_session_strictly_after_dialog_exec(self, qapp):
        """Проверка roleplay_controller: _create_new_session вызывается строго после завершения exec."""
        class MockMainWindow(RoleplayControllerMixin):
            def __init__(self):
                self.created_sessions = []
                self.current_session = None

            def _create_new_session(self, mode="roleplay", character_card=None):
                self.created_sessions.append((mode, character_card))

        window = MockMainWindow()
        test_card = CharacterCard(name="Герой", description="Описание")

        with patch("ui.dialogs.characters_gallery_dialog.CharactersGalleryDialog") as MockGalleryCls:
            mock_dlg = MagicMock()
            mock_dlg.selected_card = test_card
            # Эмулируем успешное завершение exec -> Accepted (1)
            mock_dlg.exec.return_value = QDialog.DialogCode.Accepted

            MockGalleryCls.return_value = mock_dlg

            window._open_characters_gallery_dialog()

            # exec должен быть вызван
            mock_dlg.exec.assert_called_once()
            # Сессия должна быть создана с переданной карточкой
            assert len(window.created_sessions) == 1
            mode, card = window.created_sessions[0]
            assert mode == "roleplay"
            assert card == test_card


class TestTranslationParsing:
    """Проверка надежного парсинга перевода в _translate_character_card_task."""

    def test_translation_delimiter_resilience(self, qapp):
        class MockMainWindow(RoleplayControllerMixin):
            def __init__(self):
                self.lm_client = MagicMock()
                self.task_queue = MagicMock()
                self._active_tasks = {}
                self.current_session = None
                self._status_msgs = []

            def statusBar(self):
                mock_sb = MagicMock()
                mock_sb.showMessage = lambda msg, *args: self._status_msgs.append(msg)
                return mock_sb

            def _save_current_session(self):
                pass

        window = MockMainWindow()
        card = CharacterCard(
            name="Каэла",
            description="Elven scout in the dark forest.",
            personality="Cold, cautious, highly perceptive.",
            scenario="Guiding travellers.",
            mes_example="<START>\n{{char}}: Tread quietly.",
            first_mes="Stop right there.",
            alternate_greetings=["Who goes there?"]
        )

        # Ответ модели с пробелами, разным регистром и маркдаун-обертками
        llm_reply = (
            "Вот перевод карточки:\n"
            "**=== DESCRIPTION ===**\n"
            "Эльфийская разведчица в тёмном лесу.\n"
            "===personality===\n"
            "Холодная, осторожная, предельно внимательная.\n"
            "==== SCENARIO ====\n"
            "Сопровождение путников через чащу.\n"
            "=== MES_EXAMPLE ===\n"
            "<START>\n{{char}}: Ступай бесшумно.\n"
            "=== FIRST_MES ===\n"
            "Стой на месте.\n"
            "=== ALT_GREETING_1 ===\n"
            "Кто идет?"
        )

        completed_data = []

        def on_done_cb(success, res_dict):
            completed_data.append((success, res_dict))

        with patch("services.tavern_parser.TavernParser.save_card_translations") as mock_save:
            def mock_chat_completion(*args, **kwargs):
                return llm_reply

            window.lm_client.chat_completion = mock_chat_completion
            window._translate_character_card_task(card, on_complete=on_done_cb)

            # Извлекаем коллбэк завершения задачи из зарегистрированного слушателя task_queue
            connect_calls = window.task_queue.task_finished.connect.call_args_list
            assert len(connect_calls) >= 1
            on_done_wrapper = connect_calls[-1][0][0]

            task = list(window._active_tasks.values())[0]
            result = task.func(task.kwargs.get("text"))
            on_done_wrapper(task.id, result)

            assert len(completed_data) == 1
            success, res_dict = completed_data[0]
            assert success is True
            assert res_dict["DESCRIPTION"] == "Эльфийская разведчица в тёмном лесу."
            assert res_dict["PERSONALITY"] == "Холодная, осторожная, предельно внимательная."
            assert res_dict["SCENARIO"] == "Сопровождение путников через чащу."
            assert res_dict["FIRST_MES"] == "Стой на месте."
            assert res_dict["ALT_GREETING_1"] == "Кто идет?"

            # Поля карточки гарантированно сохранены
            assert card.description_ru == "Эльфийская разведчица в тёмном лесу."
            assert card.personality_ru == "Холодная, осторожная, предельно внимательная."
            assert card.scenario_ru == "Сопровождение путников через чащу."
            assert card.first_mes_ru == "Стой на месте."
            assert card.alternate_greetings_ru == ["Кто идет?"]
            mock_save.assert_called()


class TestRuFieldsProtection:
    """Проверка защиты полей _ru от затирания при сохранении и переключении."""

    def test_save_changes_does_not_wipe_ru_fields_when_input_lacks_cyrillic(self, gallery_env):
        dlg, _, chars_dir = gallery_env
        card = CharacterCard(
            name="Лира",
            description="Original English description.",
            description_ru="Русское описание персонажа Лира.",
            first_mes="Original English greeting.",
            first_mes_ru="Русское приветствие Лиры."
        )
        card_path = chars_dir / "lyra.json"
        raw_json = {
            "name": "Лира",
            "description": card.description,
            "description_ru": card.description_ru,
            "first_mes": card.first_mes,
            "first_mes_ru": card.first_mes_ru
        }
        with open(card_path, "w", encoding="utf-8") as f:
            json.dump(raw_json, f, ensure_ascii=False)

        dlg.active_entry = {"card": card, "path": card_path}

        # Режим отображения оригинала (EN)
        dlg._showing_ru = False
        dlg.input_name.setText(card.name)
        dlg.input_desc.setPlainText(card.description)
        dlg.input_first_mes.setPlainText(card.first_mes)

        dlg._save_changes(silent=True)

        # Русские поля НЕ должны быть затерты пустыми строками
        assert card.description_ru == "Русское описание персонажа Лира."
        assert card.first_mes_ru == "Русское приветствие Лиры."

        with open(card_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        target_dict = saved_data.get("data", saved_data)
        assert target_dict.get("description_ru") == "Русское описание персонажа Лира."
        assert target_dict.get("first_mes_ru") == "Русское приветствие Лиры."

    def test_save_changes_updates_ru_only_when_new_cyrillic_provided(self, gallery_env):
        dlg, _, chars_dir = gallery_env
        card = CharacterCard(
            name="Лира",
            description="Original English description.",
            description_ru="Старое русское описание.",
            first_mes="Original greeting.",
            first_mes_ru="Старое русское приветствие."
        )
        card_path = chars_dir / "lyra.json"
        with open(card_path, "w", encoding="utf-8") as f:
            json.dump({"name": "Лира"}, f)

        dlg.active_entry = {"card": card, "path": card_path}
        dlg._showing_ru = True
        dlg.input_name.setText("Лира")
        dlg.input_desc.setPlainText("Новое улучшенное русское описание.")
        dlg.input_first_mes.setPlainText("Новое русское приветствие.")

        dlg._save_changes(silent=True)

        assert card.description_ru == "Новое улучшенное русское описание."
        assert card.first_mes_ru == "Новое русское приветствие."

    def test_on_all_done_updates_inputs_before_saving(self, gallery_env):
        dlg, parent_widget, chars_dir = gallery_env
        card = CharacterCard(
            name="Соня",
            description="Cybernetic operative.",
            first_mes="Standing by."
        )
        card_path = chars_dir / "sonya.json"
        with open(card_path, "w", encoding="utf-8") as f:
            json.dump({"name": "Соня"}, f)

        dlg.active_entry = {"card": card, "path": card_path}
        dlg.input_name.setText("Соня")
        dlg.input_desc.setPlainText(card.description)
        dlg.input_first_mes.setPlainText(card.first_mes)

        def mock_translate_task(c, on_complete=None):
            c.description_ru = "Кибернетический оперативник."
            c.first_mes_ru = "В режиме ожидания."
            if on_complete:
                on_complete(True)

        parent_widget._translate_character_card_task = mock_translate_task

        with patch("PyQt6.QtWidgets.QMessageBox.information"):
            dlg._on_translate_all_clicked()

        assert dlg._showing_ru is True
        assert dlg.input_desc.toPlainText() == "Кибернетический оперативник."
        assert dlg.input_first_mes.toPlainText() == "В режиме ожидания."
        assert card.description_ru == "Кибернетический оперативник."
        assert card.first_mes_ru == "В режиме ожидания."


class TestChatGreetingDisplay:
    """Проверка чистого отображения первого сообщения в ролевом чате."""

    def test_first_mes_ru_used_directly_without_duplicate_blocks(self, qapp):
        """Если first_mes_ru заполнено, оно используется как основное сообщение без дублирования."""
        class MockMainWindow(QWidget):
            def __init__(self):
                super().__init__()
                self.current_session = None
                self.lm_combo = MagicMock()
                self.lm_combo.currentText.return_value = "default"
                self.mode_coordinator = MagicMock()
                self.app_config = {}
                self.chat_history = []
                self.auto_translate = True
                self.active_character = None
                self.appended_messages = []
                self.enqueued_translations = []
                self.chat_list_widget = MagicMock()
                self._session_items = {}

            def _save_current_session(self): pass
            def _update_mode_ui(self): pass
            def _unlock_mode_selector(self): pass
            def _clear_chat_widgets(self): pass
            def _apply_chat_background(self): pass
            def _get_active_user_name(self): return "Игрок"
            def _remove_greeting_cycler_chip(self): pass
            def _create_greeting_cycler_chip(self, *args): pass
            def _configure_session_item_widget(self, *args): pass
            def _on_delete_session_requested(self, *args): pass
            def _on_rename_session_requested(self, *args): pass
            def _on_change_persona_requested(self, *args): pass
            def _enqueue_translation_task(self, text, widget):
                self.enqueued_translations.append(text)
            def _append_message(self, sender, text, **kwargs):
                self.appended_messages.append({"sender": sender, "text": text, **kwargs})
                return MagicMock()

        card = CharacterCard(
            name="Ария",
            first_mes="Greetings traveler, welcome to the guild.",
            first_mes_ru="Приветствую, путник! Добро пожаловать в гильдию."
        )

        win = MockMainWindow()
        try:
            with patch("services.rp_cast_manager.RPCastManager.save_cast"):
                MainWindow._create_new_session(win, mode="roleplay", character_card=card)

            assert len(win.appended_messages) == 1
            msg = win.appended_messages[0]
            # Основной текст сообщения - на русском
            assert "Приветствую, путник!" in msg["text"]
            # Нет дублирования перевода
            assert msg.get("translated_text") is None
            assert msg.get("is_showing_translation") is False
            # Повторный перевод не ставился в очередь
            assert len(win.enqueued_translations) == 0
        finally:
            SandboxManager.set_active_session_id(None)
            win.deleteLater()
