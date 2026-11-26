"""
Compact contract tests for previously uncovered UI controllers:
- HeaderStateController
- ChatSearchMixin
- TaskLifecycleMixin
- EngineerControllerMixin & AvatarControllerMixin
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from ui.controllers.header_controller import HeaderStateController
from ui.controllers.search_controller import ChatSearchMixin
from ui.controllers.task_lifecycle_controller import TaskLifecycleMixin
from ui.controllers.engineer_controller import EngineerControllerMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


def test_header_state_controller_transitions(qapp):
    ctrl = HeaderStateController()
    emitted = []
    ctrl.state_updated.connect(lambda t, s, c, a: emitted.append((t, s, c, a)))

    ctrl.sync_context("assistant", None, None)
    assert emitted[-1][2] == "#38BDF8"

    ctrl.set_forge_progress("Hires.fix", 50)
    assert "Hires.fix: 50%" in emitted[-1][1]

    ctrl.set_task_state("file", "Экспорт")
    assert emitted[-1][1] == "Экспорт..."
    assert emitted[-1][2] == "#FBBF24"

    ctrl.clear_active_task()
    char = SimpleNamespace(name="Серафина", avatar_path=None)
    with patch("services.session_manager.SessionManager.get_active_user_persona", return_value={"name": "Игрок"}):
        ctrl.sync_context("roleplay", char, {"id": "s1"})
    assert emitted[-1][0] == "Серафина"
    assert emitted[-1][2] == "#A78BFA"


def test_chat_search_mixin_navigation(qapp):
    class DummySearchHost(QWidget, ChatSearchMixin):
        def __init__(self):
            super().__init__()
            self.search_bar_widget = MagicMock()
            self.search_bar_widget.isVisible.return_value = True
            self.input_box = MagicMock()
            self._search_matching_widgets = []
            self._current_search_idx = -1
            self._widgets = [
                SimpleNamespace(raw_text="Привет мир", text_browser=None),
                SimpleNamespace(raw_text="Другой текст", text_browser=None),
                SimpleNamespace(raw_text="Мир неона", text_browser=None),
            ]
            self.navigated = []

        def _get_chat_message_widgets(self):
            return self._widgets

        def _navigate_search_match(self, idx):
            self.navigated.append(idx)

    host = DummySearchHost()
    try:
        host._on_search_query_changed("мир")
        assert len(host._search_matching_widgets) == 2
        host.search_bar_widget.set_match_info.assert_called_with(1, 2)
        assert host.navigated == [0]

        host._search_next()
        assert host._current_search_idx == 1
        host._search_prev()
        assert host._current_search_idx == 0

        host._close_search_bar()
        assert host._search_matching_widgets == []
        host.search_bar_widget.setVisible.assert_called_with(False)
    finally:
        host.deleteLater()


def test_task_lifecycle_translation_guards_and_cached_greeting(qapp):
    class DummyLifecycleHost(QWidget, TaskLifecycleMixin):
        def __init__(self):
            super().__init__()
            self.current_session = {"character_data": {"id": "c1", "name": "Aria", "first_mes": "Hello!", "first_mes_ru": "Привет!"}}
            self._status = MagicMock()
            self.task_queue = MagicMock()

        def statusBar(self):
            return self._status

        def _update_message_translation_in_session(self, msg, text):
            pass

    host = DummyLifecycleHost()
    msg_widget = MagicMock()
    try:
        # 1. Code block must be rejected
        host._enqueue_translation_task("```python\nprint(1)\n```", msg_widget)
        host.task_queue.enqueue.assert_not_called()

        # 2. System log must be rejected
        host._enqueue_translation_task("[Системный результат]: OK", msg_widget)
        host.task_queue.enqueue.assert_not_called()

        # 3. Cached greeting skips queue and applies translation immediately
        host._enqueue_translation_task("Hello!", msg_widget, is_greeting=True)
        msg_widget.set_translation.assert_called_once_with("Привет!")
        host.task_queue.enqueue.assert_not_called()
    finally:
        host.deleteLater()


def test_engineer_controller_ephemeral_doc_and_mode_check(qapp):
    class DummyEngHost(QWidget, EngineerControllerMixin):
        def __init__(self):
            super().__init__()
            self.current_session = {"mode": "engineer"}
            self.btn_engine_mode = MagicMock()
            self.btn_engine_mode.isChecked.return_value = True

    host = DummyEngHost()
    try:
        assert host.is_engineer_mode_active() is True
        assert host._is_engineering_session() is True
        host.current_session = {"mode": "assistant"}
        assert host._is_engineering_session() is False
        assert host._is_ephemeral_doc_request("сделай excel таблицу") is True
        assert host._is_ephemeral_doc_request("напиши веб-сервер на fastapi") is False
    finally:
        host.deleteLater()
