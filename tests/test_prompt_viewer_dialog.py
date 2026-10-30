# -*- coding: utf-8 -*-
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QWidget, QMenu
from PyQt6.QtGui import QGuiApplication

from services.tavern_models import CharacterCard
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from ui.dialogs.prompt_viewer_dialog import PromptViewerDialog
from ui.controllers.avatar_controller import AvatarControllerMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestRoleplayPromptBuilderSystemPrompt:
    """Проверка формирования эталонного системного промпта и поддержки custom_system_prompt."""

    def test_default_system_prompt_generation(self):
        card = CharacterCard(
            name="Элария",
            description="Магистр иллюзий",
            personality="Холодная, рассудительная",
            scenario="Таверна на краю мира",
            system_prompt="Никогда не выходи из роли мага."
        )
        prompt = RoleplayPromptBuilder.get_default_system_prompt(card, user_name="Алекс", world_facts="В мире царит вечная ночь.")
        assert "Элария" in prompt
        assert "Алекс" in prompt
        assert "Магистр иллюзий" in prompt

    def test_build_with_custom_system_prompt(self):
        card = CharacterCard(name="Элария")
        custom = "Кастомный промпт для {{char}} от {{user}}."
        res = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Алекс",
            custom_system_prompt=custom
        )
        assert res == "Кастомный промпт для Элария от Алекс."

    def test_build_without_custom_system_prompt(self):
        card = CharacterCard(name="Элария", description="Магистр иллюзий")
        res = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Алекс",
            custom_system_prompt=None
        )
        assert "Элария" in res
        assert "Алекс" in res


class TestPromptViewerDialog:
    """Проверка диалога PromptViewerDialog на компонентах QFluentWidgets."""

    def test_dialog_initialization_default(self, qapp):
        card = CharacterCard(name="Селеста", description="Воительница")
        parent = QWidget()
        dlg = PromptViewerDialog(parent=parent, character_card=card, user_name="Роман")

        assert dlg.char_name == "Селеста"
        assert dlg.user_name == "Роман"
        assert dlg.default_prompt == RoleplayPromptBuilder.get_default_system_prompt(card, "Роман").strip()
        assert dlg.prompt_edit.toPlainText().strip() == dlg.default_prompt
        assert dlg.get_configured_prompt() is None  # Дефолт = сброс ключа в сессии

    def test_dialog_initialization_with_custom(self, qapp):
        card = CharacterCard(name="Селеста")
        parent = QWidget()
        custom = "[Мой уникальный системный промпт]"
        dlg = PromptViewerDialog(parent=parent, character_card=card, user_name="Роман", current_prompt=custom)

        assert dlg.prompt_edit.toPlainText().strip() == custom
        assert dlg.get_configured_prompt() == custom

    def test_reset_to_default(self, qapp):
        card = CharacterCard(name="Селеста")
        parent = QWidget()
        dlg = PromptViewerDialog(parent=parent, character_card=card, user_name="Роман", current_prompt="[Изменено]")

        assert dlg.prompt_edit.toPlainText().strip() == "[Изменено]"
        dlg.btn_reset_default.click()
        assert dlg.prompt_edit.toPlainText().strip() == dlg.default_prompt
        assert dlg.get_configured_prompt() is None

    def test_copy_button(self, qapp):
        card = CharacterCard(name="Селеста")
        parent = QWidget()
        dlg = PromptViewerDialog(parent=parent, character_card=card, user_name="Роман")

        with patch.object(QGuiApplication, "clipboard") as mock_clip_fn:
            mock_clip = MagicMock()
            mock_clip_fn.return_value = mock_clip
            dlg.btn_copy.click()
            mock_clip.setText.assert_called_once_with(dlg.default_prompt)


class DummyWindow(AvatarControllerMixin, QWidget):
    """Тестовый контейнер для проверки контекстного меню."""
    def __init__(self):
        super().__init__()
        self.current_mode = "roleplay"
        self.current_session = {
            "id": "test-session-1",
            "mode": "roleplay",
            "user_persona_id": None
        }
        self.active_character = CharacterCard(name="Тестовый персонаж")
        self._open_model_sampling_dialog = MagicMock()
        self._on_search_clicked = MagicMock()
        self._on_media_clicked = MagicMock()
        self._on_open_chat_files_clicked = MagicMock()
        self._on_change_avatar_clicked = MagicMock()
        self._on_reset_avatar_clicked = MagicMock()
        self._on_rename_clicked = MagicMock()


class TestAvatarControllerPromptsMenu:
    """Проверка добавления пункта контекстного меню и условий отображения."""

    def test_menu_actions_in_roleplay_mode(self, qapp):
        win = DummyWindow()
        win.current_mode = "roleplay"

        created_actions = []
        with patch.object(QMenu, "addAction", side_effect=lambda text: created_actions.append(text) or MagicMock()):
            with patch.object(QMenu, "exec"):
                win._show_avatar_menu(None)

        assert any("📜 Промпты..." in a for a in created_actions), "Пункт '📜 Промпты...' должен быть в меню для roleplay"
        assert any("⚓ Якорь сцены..." in a for a in created_actions), "Пункт '⚓ Якорь сцены...' должен быть в меню для roleplay"

    def test_menu_actions_not_in_assistant_mode(self, qapp):
        win = DummyWindow()
        win.current_mode = "assistant"
        win.current_session["mode"] = "assistant"

        created_actions = []
        with patch.object(QMenu, "addAction", side_effect=lambda text: created_actions.append(text) or MagicMock()):
            with patch.object(QMenu, "exec"):
                win._show_avatar_menu(None)

        assert not any("📜 Промпты..." in a for a in created_actions), "Пункт '📜 Промпты...' НЕ должен отображаться в assistant"
        assert not any("⚓ Якорь сцены..." in a for a in created_actions), "Пункт '⚓ Якорь сцены...' НЕ должен отображаться в assistant"
