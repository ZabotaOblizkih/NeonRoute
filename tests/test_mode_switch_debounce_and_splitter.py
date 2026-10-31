import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QSplitter, QWidget

# Ensure QApplication exists
app = QApplication.instance() or QApplication([])

from ui.main_window import MainWindow


class TestModeSwitchDebounceAndSplitter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Patch init side effects to test MainWindow methods safely
        with patch.object(MainWindow, "_setup_ui"), \
             patch.object(MainWindow, "_setup_shortcuts"), \
             patch.object(MainWindow, "_setup_signals"), \
             patch.object(MainWindow, "_start_background_services"), \
             patch.object(MainWindow, "_update_header_for_current_state"):
            cls.mw = MainWindow()

    def test_debounce_timer_initialized(self):
        self.assertTrue(hasattr(self.mw, "_model_switch_timer"))
        self.assertTrue(self.mw._model_switch_timer.isSingleShot())
        self.assertEqual(self.mw._model_switch_timer.interval(), 400)

    def test_debounced_model_switch_execution(self):
        self.mw._pending_switch_model = "test-model-v1"
        self.mw._pending_switch_mode = "rp"
        self.mw.lm_client = MagicMock()
        self.mw.lm_client.ensure_model_loaded = MagicMock()

        with patch("threading.Thread") as mock_thread:
            mock_inst = MagicMock()
            mock_thread.return_value = mock_inst

            self.mw._execute_debounced_model_switch()

            mock_thread.assert_called_once()
            _, kwargs = mock_thread.call_args
            self.assertEqual(kwargs.get("name"), "ModelSwitchWorker")
            self.assertTrue(kwargs.get("daemon"))
            mock_inst.start.assert_called_once()

    def test_sync_splitter_layout_engineer(self):
        self.mw.main_splitter = MagicMock()
        self.mw.main_splitter.width.return_value = 1600
        self.mw.workspace_inspector = MagicMock()

        self.mw._sync_splitter_layout(is_engineer=True)

        self.mw.workspace_inspector.setVisible.assert_called_with(True)
        self.mw.workspace_inspector.refresh_files.assert_called_once()

        # sidebar = 270, inspector = 380, chat = 1600 - 270 - 380 = 950
        self.mw.main_splitter.setSizes.assert_called_with([270, 950, 380])

    def test_sync_splitter_layout_non_engineer(self):
        self.mw.main_splitter = MagicMock()
        self.mw.main_splitter.width.return_value = 1200
        self.mw.workspace_inspector = MagicMock()

        self.mw._sync_splitter_layout(is_engineer=False)

        self.mw.workspace_inspector.setVisible.assert_called_with(False)
        # sidebar = 270, chat = 1200 - 270 = 930
        self.mw.main_splitter.setSizes.assert_called_with([270, 930, 0])


if __name__ == "__main__":
    unittest.main()
