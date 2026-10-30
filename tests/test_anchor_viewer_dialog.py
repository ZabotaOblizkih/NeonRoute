# -*- coding: utf-8 -*-
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QWidget, QMenu
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QGuiApplication

from services.tavern_models import CharacterCard
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from ui.dialogs.anchor_viewer_dialog import AnchorViewerDialog
from ui.controllers.avatar_controller import AvatarControllerMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestRoleplayPromptBuilderDepthAnchor:
    """Проверка генерации абстрактных шаблонов якорей и кастомизации."""

    def test_default_depth_anchors_do_not_quote_user_query(self):
        archetypes = [
            CharacterCard(name="Система", is_system=True),
            CharacterCard(name="Детектив", is_detective=True),
            CharacterCard(name="Мика", is_dating=True),
            CharacterCard(name="Нечто", is_horror=True),
            CharacterCard(name="ГМ", is_dm=True),
            CharacterCard(name="Новелла", is_cyoa=True),
            CharacterCard(name="Отряд", is_group=True),
            CharacterCard(name="Селеста")
        ]
        for card in archetypes:
            anchor = RoleplayPromptBuilder.get_default_depth_anchor(card, user_name="Игрок")
            assert "[Системная директива сцены:" in anchor
            # Не должно быть цитирования реплик
            assert "«" not in anchor
            assert "Реплика" not in anchor

    def test_build_depth_anchor_with_custom(self):
        card = CharacterCard(name="Селеста")
        custom = "[Кастомный якорь сцены]"
        res = RoleplayPromptBuilder.build_depth_anchor(card, user_name="Игрок", custom_anchor=custom)
        assert res.strip() == custom
        assert res.startswith("\n\n")

    def test_build_depth_anchor_default_fallback(self):
        card = CharacterCard(name="Селеста")
        default = RoleplayPromptBuilder.get_default_depth_anchor(card, "Игрок")
        res1 = RoleplayPromptBuilder.build_depth_anchor(card, user_name="Игрок", custom_anchor=None)
        res2 = RoleplayPromptBuilder.build_depth_anchor(card, user_name="Игрок", custom_anchor="")
        assert res1 == default
        assert res2 == default


class TestAnchorViewerDialog:
    """Проверка диалога AnchorViewerDialog на компонентах QFluentWidgets."""

    def test_dialog_initialization_default(self, qapp):
        card = CharacterCard(name="Селеста")
        parent = QWidget()
        dlg = AnchorViewerDialog(parent=parent, character_card=card, user_name="Роман")

        assert dlg.char_name == "Селеста"
        assert dlg.user_name == "Роман"
        assert dlg.default_anchor == RoleplayPromptBuilder.get_default_depth_anchor(card, "Роман").strip()
        assert dlg.anchor_edit.toPlainText().strip() == dlg.default_anchor
        assert dlg.get_configured_anchor() is None  # Дефолт = сброс ключа в сессии

    def test_dialog_initialization_with_custom(self, qapp):
        card = CharacterCard(name="Селеста")
        parent = QWidget()
        custom = "[Мой уникальный якорь]"
        dlg = AnchorViewerDialog(parent=parent, character_card=card, user_name="Роман", current_anchor=custom)

        assert dlg.anchor_edit.toPlainText().strip() == custom
        assert dlg.get_configured_anchor() == custom

    def test_reset_to_default(self, qapp):
        card = CharacterCard(name="Селеста")
        parent = QWidget()
        dlg = AnchorViewerDialog(parent=parent, character_card=card, user_name="Роман", current_anchor="[Изменено]")

        assert dlg.anchor_edit.toPlainText().strip() == "[Изменено]"
        dlg.btn_reset_default.click()
        assert dlg.anchor_edit.toPlainText().strip() == dlg.default_anchor
        assert dlg.get_configured_anchor() is None

    def test_copy_button(self, qapp):
        card = CharacterCard(name="Селеста")
        parent = QWidget()
        dlg = AnchorViewerDialog(parent=parent, character_card=card, user_name="Роман")

        dlg.btn_copy.click()
        clipboard_text = QGuiApplication.clipboard().text()
        assert clipboard_text == dlg.default_anchor


class DummyMainWindow(QWidget, AvatarControllerMixin):
    def __init__(self, mode="roleplay", card=None, session=None):
        super().__init__()
        self.current_mode = mode
        self.active_character = card
        self.current_session = session or {"id": "sess_1", "mode": mode}
        self.header_avatar_lbl = None

    def _on_change_avatar_clicked(self): pass
    def _open_model_sampling_dialog(self): pass
    def _on_search_clicked(self): pass
    def _on_media_clicked(self): pass
    def _on_open_chat_files_clicked(self): pass
    def _on_reset_avatar_clicked(self): pass
    def _on_rename_clicked(self): pass


class TestAvatarMenuAndController:
    """Проверка контекстного меню аватара и открытия диалога якоря."""

    def test_anchor_menu_action_only_in_roleplay(self, qapp):
        card = CharacterCard(name="Селеста")

        # 1. Режим roleplay -> пункт должен быть
        win_rp = DummyMainWindow(mode="roleplay", card=card)
        created_actions = []

        with patch("PyQt6.QtWidgets.QMenu.addAction", side_effect=lambda *args: created_actions.append(args[0]) or MagicMock()):
            with patch("PyQt6.QtWidgets.QMenu.exec"):
                win_rp._show_avatar_menu(QPoint(0, 0))

        assert any("Якорь сцены" in str(act) for act in created_actions)

        # 2. Режим assistant -> пункта НЕ должно быть
        created_actions.clear()
        win_as = DummyMainWindow(mode="assistant", card=card)
        with patch("PyQt6.QtWidgets.QMenu.addAction", side_effect=lambda *args: created_actions.append(args[0]) or MagicMock()):
            with patch("PyQt6.QtWidgets.QMenu.exec"):
                win_as._show_avatar_menu(QPoint(0, 0))

        assert not any("Якорь сцены" in str(act) for act in created_actions)

        # 3. Режим engineer -> пункта НЕ должно быть
        created_actions.clear()
        win_eng = DummyMainWindow(mode="engineer", card=card)
        with patch("PyQt6.QtWidgets.QMenu.addAction", side_effect=lambda *args: created_actions.append(args[0]) or MagicMock()):
            with patch("PyQt6.QtWidgets.QMenu.exec"):
                win_eng._show_avatar_menu(QPoint(0, 0))

        assert not any("Якорь сцены" in str(act) for act in created_actions)

    def test_open_anchor_dialog_save_custom(self, qapp):
        card = CharacterCard(name="Селеста")
        session = {"id": "sess_rp", "mode": "roleplay"}
        win = DummyMainWindow(mode="roleplay", card=card, session=session)

        with patch("ui.controllers.avatar_controller.AnchorViewerDialog") as MockDialog:
            mock_dlg_instance = MagicMock()
            mock_dlg_instance.exec.return_value = True
            mock_dlg_instance.get_configured_anchor.return_value = "[Новая кастомная директива]"
            MockDialog.return_value = mock_dlg_instance

            with patch("services.session_manager.SessionManager.save_session") as mock_save:
                win._open_anchor_dialog()
                assert win.current_session.get("custom_depth_anchor") == "[Новая кастомная директива]"
                mock_save.assert_called_once_with(win.current_session)

    def test_open_anchor_dialog_reset_to_default_removes_key(self, qapp):
        card = CharacterCard(name="Селеста")
        session = {"id": "sess_rp", "mode": "roleplay", "custom_depth_anchor": "[Старый кастом]"}
        win = DummyMainWindow(mode="roleplay", card=card, session=session)

        with patch("ui.controllers.avatar_controller.AnchorViewerDialog") as MockDialog:
            mock_dlg_instance = MagicMock()
            mock_dlg_instance.exec.return_value = True
            mock_dlg_instance.get_configured_anchor.return_value = None  # Сброс
            MockDialog.return_value = mock_dlg_instance

            with patch("services.session_manager.SessionManager.save_session") as mock_save:
                win._open_anchor_dialog()
                assert "custom_depth_anchor" not in win.current_session
                mock_save.assert_called_once_with(win.current_session)
