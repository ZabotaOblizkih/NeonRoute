import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile
import shutil

from services.mode_coordinator import ModeCoordinator, AppMode
from services.session_manager import SessionManager


class TestModeIsolation(unittest.TestCase):

    def setUp(self):
        self.mock_mw = MagicMock()
        self.mock_mw.chat_history = [{"role": "user", "content": "Previous prompt with <<TOOL:run>>"}]
        self.mock_mw.current_mode = "assistant"
        self.mock_mw._engineering_mode_active = False
        self.mock_mw.current_session = {
            "id": "test_sess_prev",
            "mode": "assistant",
            "messages": [{"role": "user", "text": "Old message"}],
            "chat_history": list(self.mock_mw.chat_history)
        }
        self.mock_mw._active_tasks = {"task_1": "dummy"}
        self.mock_mw.task_queue = MagicMock()
        self.mock_mw.lm_client = MagicMock()
        self.mock_mw.abort_generation = MagicMock()
        self.mock_mw._clear_chat_widgets = MagicMock()
        self.mock_mw._session_items = {}
        self.mock_mw.chat_list_widget = MagicMock()
        self.mock_mw.lm_combo = MagicMock()
        self.mock_mw.lm_combo.currentText.return_value = "default_model"
        self.mock_mw.lm_combo.findText.return_value = 0
        self.mock_mw.lm_combo.count.return_value = 1

        self.coordinator = ModeCoordinator(self.mock_mw)

    def tearDown(self):
        from config import HISTORY_DIR
        for p in HISTORY_DIR.glob("chat_test_*.json"):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def test_session_manager_singleton_and_save(self):
        sm = SessionManager.get_instance()
        self.assertIsInstance(sm, SessionManager)
        self.assertIs(sm, SessionManager.get_instance())

        new_sess = sm.create_session(mode="assistant")
        self.assertIn("id", new_sess)
        self.assertEqual(new_sess["mode"], "assistant")

        # Save by session id string
        sess_id = new_sess["id"]
        saved_path = sm.save_session(sess_id)
        self.assertIsNotNone(saved_path)
        self.assertTrue(Path(saved_path).exists())

        # Cleanup
        SessionManager.delete_session(sess_id)

    def test_teardown_aborts_generation_and_saves_outgoing_session(self):
        self.coordinator._teardown_current_mode()

        self.mock_mw.abort_generation.assert_called_once()
        self.assertEqual(len(self.mock_mw._active_tasks), 0)

    def test_transition_isolation_clears_chat_history(self):
        # Setup initial chat_history
        self.mock_mw.chat_history = [{"role": "assistant", "content": "[IMAGE_PROMPT: 1girl] <<TOOL:test>>"}]

        # Transition to roleplay with an isolated session
        rp_session = SessionManager.create_session(mode="roleplay")
        rp_session["chat_history"] = []
        rp_session["messages"] = []

        res = self.coordinator.transition_to(AppMode.ROLEPLAY, session_data=rp_session)
        self.assertTrue(res)
        self.assertEqual(self.coordinator.current_mode, AppMode.ROLEPLAY)
        self.assertEqual(self.mock_mw.current_mode, "roleplay")
        self.assertFalse(self.mock_mw._engineering_mode_active)

        # chat_history must be empty (isolated from previous mode's messages)
        self.assertEqual(self.mock_mw.chat_history, [])
        SessionManager.delete_session(rp_session["id"])

    def test_transition_without_session_data_finds_or_creates_isolated_session(self):
        self.mock_mw.chat_history = [{"role": "assistant", "content": "Old RP text"}]

        # Transition to ENGINEER mode with session_data=None
        res = self.coordinator.transition_to(AppMode.ENGINEER, session_data=None)
        self.assertTrue(res)
        self.assertEqual(self.coordinator.current_mode, AppMode.ENGINEER)
        self.assertTrue(self.mock_mw._engineering_mode_active)

        # Check that active session is an engineering session
        curr = self.mock_mw.current_session
        self.assertIsNotNone(curr)
        self.assertTrue(curr.get("type") == "engineering" or curr.get("mode") in ("engineer", "engineering"))
        self.mock_mw._clear_chat_widgets.assert_called()

        if curr.get("id"):
            SessionManager.delete_session(curr["id"])

    def test_teardown_engineer_resets_flags(self):
        self.coordinator._current_mode = AppMode.ENGINEER
        self.mock_mw._pending_workspace_file = "test.py"
        self.mock_mw._script_executed_this_turn = True
        self.mock_mw._tool_executed_this_turn = True
        self.mock_mw._doc_created_this_turn = True

        self.coordinator._teardown_engineer_subsystem()

        self.assertIsNone(self.mock_mw._pending_workspace_file)
        self.assertFalse(self.mock_mw._script_executed_this_turn)
        self.assertFalse(self.mock_mw._tool_executed_this_turn)
        self.assertFalse(self.mock_mw._doc_created_this_turn)

    def test_teardown_roleplay_resets_flags(self):
        self.coordinator._current_mode = AppMode.ROLEPLAY
        self.mock_mw.active_character = MagicMock()
        self.mock_mw._pending_scene_art_request = "scene_1"
        self.mock_mw._remove_greeting_cycler_chip = MagicMock()

        self.coordinator._teardown_roleplay_subsystem()

        self.assertIsNone(self.mock_mw.active_character)
        self.assertIsNone(self.mock_mw._pending_scene_art_request)
        self.mock_mw._remove_greeting_cycler_chip.assert_called_once()


    def test_reentrancy_protection(self):
        self.coordinator._is_transitioning = True
        res = self.coordinator.transition_to(AppMode.ENGINEER)
        self.assertFalse(res)
        self.assertTrue(self.coordinator._is_transitioning)

        # After reset, transition proceeds
        self.coordinator._is_transitioning = False
        res = self.coordinator.transition_to(AppMode.ROLEPLAY)
        self.assertTrue(res)
        self.assertFalse(self.coordinator._is_transitioning)
        if self.mock_mw.current_session and self.mock_mw.current_session.get("id"):
            SessionManager.delete_session(self.mock_mw.current_session["id"])

    def test_sync_splitter_layout_called_by_boot(self):
        self.mock_mw._sync_splitter_layout = MagicMock()
        self.coordinator._boot_target_mode(AppMode.ENGINEER)
        self.mock_mw._sync_splitter_layout.assert_called_with(True)

        self.mock_mw._sync_splitter_layout.reset_mock()
        self.coordinator._boot_target_mode(AppMode.ASSISTANT)
        self.mock_mw._sync_splitter_layout.assert_called_with(False)


if __name__ == "__main__":
    unittest.main()
