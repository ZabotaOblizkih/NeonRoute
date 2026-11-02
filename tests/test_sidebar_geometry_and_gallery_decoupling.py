import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from PyQt6.QtWidgets import QApplication, QSizePolicy, QWidget
from PyQt6.QtCore import Qt

app = QApplication.instance() or QApplication([])

from ui.components.sidebar_widget import SidebarWidget
from ui.main_window import MainWindow


class TestSidebarGeometryAndGalleryDecoupling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(MainWindow, "_setup_ui"), \
             patch.object(MainWindow, "_setup_shortcuts"), \
             patch.object(MainWindow, "_setup_signals"), \
             patch.object(MainWindow, "_start_background_services"), \
             patch.object(MainWindow, "_update_header_for_current_state"):
            cls.mw = MainWindow()

    def setUp(self):
        self.dummy_parent = QWidget()
        self.dummy_parent._open_campaign_info_dialog = MagicMock()
        self.dummy_parent._on_chat_list_item_clicked = MagicMock()
        self.sidebar = SidebarWidget(window=self.dummy_parent)

    def tearDown(self):
        self.sidebar.deleteLater()
        self.dummy_parent.deleteLater()

    def test_sidebar_geometry_constraints(self):
        """Проверяем, что ширина кнопок и карточки жестко ограничена для защиты сплиттера."""
        self.assertLessEqual(self.sidebar.char_card_widget.maximumWidth(), 260)
        self.assertLessEqual(self.sidebar.btn_campaign_info.maximumWidth(), 240)
        self.assertLessEqual(self.sidebar.btn_load_character.maximumWidth(), 240)

        # Политика размеров предотвращает распирание sizeHint
        self.assertEqual(self.sidebar.btn_campaign_info.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Preferred)
        self.assertEqual(self.sidebar.btn_campaign_info.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Fixed)
        self.assertEqual(self.sidebar.btn_load_character.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Preferred)
        self.assertEqual(self.sidebar.btn_load_character.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Fixed)

    def test_character_gallery_requested_signal_emitted(self):
        """Проверяем, что клик по btn_load_character испускает pyqtSignal character_gallery_requested."""
        mock_slot = MagicMock()
        self.sidebar.character_gallery_requested.connect(mock_slot)

        self.sidebar.btn_load_character.click()
        mock_slot.assert_called_once()

    def test_update_companion_state_non_roleplay(self):
        """В не-ролевом режиме карточка персонажа скрыта."""
        self.sidebar.update_companion_state(is_roleplay=False)
        self.assertTrue(self.sidebar.char_card_widget.isHidden())

    def test_update_companion_state_no_character(self):
        """В ролевом режиме без выбранного персонажа."""
        self.sidebar.update_companion_state(is_roleplay=True, active_character=None)
        self.assertFalse(self.sidebar.char_card_widget.isHidden())
        self.assertIn("Компаньон не выбран", self.sidebar.char_name_lbl.text())
        self.assertFalse(self.sidebar.btn_campaign_info.isEnabled())
        self.assertTrue(self.sidebar.btn_load_character.isEnabled())

    def test_update_companion_state_active_character_unlocked(self):
        """В ролевом режиме с персонажем до отправки сообщений компаньона можно сменить."""
        char = SimpleNamespace(name="Эйла")
        self.sidebar.update_companion_state(is_roleplay=True, active_character=char, has_user_messages=False)
        self.assertFalse(self.sidebar.char_card_widget.isHidden())
        self.assertIn("Эйла", self.sidebar.char_name_lbl.text())
        self.assertTrue(self.sidebar.btn_campaign_info.isEnabled())
        self.assertTrue(self.sidebar.btn_load_character.isEnabled())
        self.assertIn("Сменить компаньона", self.sidebar.btn_load_character.text())

    def test_update_companion_state_active_character_locked(self):
        """После отправки сообщений смена компаньона блокируется."""
        char = SimpleNamespace(name="Кай")
        self.sidebar.update_companion_state(is_roleplay=True, active_character=char, has_user_messages=True)
        self.assertFalse(self.sidebar.char_card_widget.isHidden())
        self.assertIn("Кай", self.sidebar.char_name_lbl.text())
        self.assertFalse(self.sidebar.btn_load_character.isEnabled())
        self.assertIn("Персонаж зафиксирован", self.sidebar.btn_load_character.text())

    def test_main_window_signal_connection(self):
        """Проверяем подключение character_gallery_requested в _setup_signals."""
        self.mw.sidebar = MagicMock()
        self.mw.sidebar.character_gallery_requested = MagicMock()
        self.mw._import_character = MagicMock()
        self.mw.task_queue = MagicMock()
        self.mw.system_monitor = MagicMock()
        self.mw.stt_service = MagicMock()
        self.mw.lm_client = MagicMock()
        self.mw.process_manager = MagicMock()
        self.mw.health_worker = MagicMock()
        self.mw.lm_combo = None

        MainWindow._setup_signals(self.mw)
        self.mw.sidebar.character_gallery_requested.connect.assert_called_with(self.mw._import_character)

    def test_main_window_update_mode_ui_delegates_to_sidebar(self):
        """_update_mode_ui делегирует отображение карточки сайдбару."""
        self.mw.sidebar = MagicMock()
        self.mw.is_engineer_mode_active = MagicMock(return_value=False)
        self.mw.current_mode = "roleplay"
        self.mw.current_session = {"messages": [{"is_user": True}]}
        self.mw.active_character = SimpleNamespace(name="Кай")
        self.mw.statusBar = MagicMock()
        self.mw._update_header_for_current_state = MagicMock()
        self.mw._sync_splitter_layout = MagicMock()

        with patch("PyQt6.QtCore.QTimer.singleShot") as mock_single_shot:
            self.mw._update_mode_ui()
            self.mw.sidebar.update_companion_state.assert_called_once_with(
                is_roleplay=True,
                active_character=self.mw.active_character,
                has_user_messages=True
            )
            mock_single_shot.assert_called_once()

    def test_roleplay_controller_gallery_dialog_reentrancy_guard(self):
        """Защита от повторного открытия экземпляров диалога галереи."""
        from ui.controllers.roleplay_controller import RoleplayControllerMixin

        class DummyRP(RoleplayControllerMixin):
            def __init__(self):
                self._active_gallery_dialog = None
                self._sync_splitter_layout = MagicMock()
                self.is_engineer_mode_active = MagicMock(return_value=False)

        ctrl = DummyRP()
        existing_dialog = MagicMock()
        existing_dialog.isVisible.return_value = True
        ctrl._active_gallery_dialog = existing_dialog

        ctrl._open_characters_gallery_dialog()
        existing_dialog.activateWindow.assert_called_once()
        existing_dialog.raise_.assert_called_once()


if __name__ == "__main__":
    unittest.main()
