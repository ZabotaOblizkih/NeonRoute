"""
End-to-End интеграционные тесты архитектуры синхронизации состояния NeonRoute.
Проверяют единый источник правды (Single Source of Truth):
_sync_turn -> session.json -> _mount_session -> ContextWindowBuilder.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

import services.session_manager as sm
from services.session_manager import SessionManager
from services.context_window_builder import ContextWindowBuilder
from ui.controllers.chat_flow_controller import ChatFlowController


class DummySyncController:
    """Облегченный мок-контроллер с реальной реализацией _sync_turn."""

    def __init__(self, session_data: dict):
        self.current_session = session_data
        self.chat_history = list(session_data.get("chat_history", []))
        self.active_character = None
        self.current_mode = "assistant"
        self._pending_knowledge_sources = None
        self._pending_thought_data = None

    _sync_turn = ChatFlowController._sync_turn


@pytest.fixture
def isolated_session_dir(tmp_path, monkeypatch):
    """Изолирует директорию истории сессий в tmp_path."""
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sm, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(SessionManager, "_sessions_cache", {})
    return history_dir


class TestSyncArchitectureE2E:

    def test_sync_turn_persists_chat_history_to_disk(self, isolated_session_dir):
        sess_id = "test-sync-uuid-001"
        raw_session = {
            "id": sess_id,
            "mode": "assistant",
            "title": "Тестовый чат",
            "messages": [],
            "chat_history": []
        }
        controller = DummySyncController(raw_session)

        controller._sync_turn(role="user", content="Вопрос 1", replace_last=False)

        thought_data = {"content": "Анализ фактов...", "duration": 1.2, "token_count": 15}
        controller._sync_turn(
            role="assistant",
            content="Ответ 1",
            replace_last=False,
            thought=thought_data,
            knowledge_sources=[{"filename": "architecture.md", "chunk_id": "c1"}]
        )

        assert len(controller.chat_history) == 2
        assert controller.chat_history[0] == {"role": "user", "content": "Вопрос 1"}
        assert "<think>\nАнализ фактов...\n</think>\n\nОтвет 1" in controller.chat_history[1]["content"]
        assert controller.current_session["chat_history"] == controller.chat_history

        disk_file = isolated_session_dir / f"chat_{sess_id}.json"
        assert disk_file.exists(), "Файл сессии не был сохранен на диск"
        
        with open(disk_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)

        assert len(disk_data["chat_history"]) == 2, "chat_history на диске остался пустым!"
        assert disk_data["chat_history"] == controller.chat_history
        assert len(disk_data["messages"]) == 2
        assert disk_data["messages"][1]["thought"]["content"] == "Анализ фактов..."
        assert disk_data["messages"][1]["knowledge_sources"][0]["filename"] == "architecture.md"

    def test_sliding_cot_preservation_across_turns(self):
        raw_history = [
            {"role": "user", "content": "Шаг 1"},
            {"role": "assistant", "content": "<think>\nСтарые мысли 1\n</think>\n\nОтвет 1"},
            {"role": "user", "content": "Шаг 2"},
            {"role": "assistant", "content": "<think>\nСвежие мысли 2\n</think>\n\nОтвет 2"},
            {"role": "user", "content": "Шаг 3"}
        ]

        normalized = ContextWindowBuilder.prepare_history_for_llm(
            history=raw_history,
            enable_reasoning=True,
            keep_last_cot=True
        )

        assert len(normalized) == 5
        assert "<think>" not in normalized[1]["content"]
        assert normalized[1]["content"] == "Ответ 1"
        assert "<think>\nСвежие мысли 2\n</think>\n\nОтвет 2" in normalized[3]["content"]
        assert normalized[4]["content"] == "Шаг 3"

    def test_mount_session_restores_complete_history(self, isolated_session_dir):
        sess_id = "test-mount-002"
        saved_history = [
            {"role": "user", "content": "Вопрос"},
            {"role": "assistant", "content": "<think>мысли</think>\nОтвет"}
        ]
        session_payload = {
            "id": sess_id,
            "mode": "assistant",
            "messages": [
                {"role": "user", "text": "Вопрос", "is_user": True},
                {"role": "assistant", "text": "Ответ", "is_user": False}
            ],
            "chat_history": saved_history
        }
        SessionManager.save_session(session_payload)

        loaded_session = SessionManager.load_session(sess_id)
        mock_mw = MagicMock()
        mock_mw.current_session = loaded_session
        mock_mw.chat_history = list(loaded_session.get("chat_history", []))

        assert len(mock_mw.chat_history) == 2
        assert mock_mw.chat_history == saved_history
        assert "<think>" in mock_mw.chat_history[1]["content"]
