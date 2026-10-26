"""
Выделенный регрессионный тест бага:
'Исчезновение тегов <think> и стирание chat_history после 2-3 шагов диалога'.
"""

import json
from unittest.mock import MagicMock
import pytest

import services.session_manager as sm
from services.session_manager import SessionManager
from services.context_window_builder import ContextWindowBuilder
from ui.controllers.chat_flow_controller import ChatFlowController


@pytest.fixture
def temp_history_env(tmp_path, monkeypatch):
    h_dir = tmp_path / "history"
    h_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sm, "HISTORY_DIR", h_dir)
    monkeypatch.setattr(SessionManager, "_sessions_cache", {})
    return h_dir


def test_regression_chat_history_not_empty_after_turn_sync(temp_history_env):
    session_id = "regression-check-uuid"
    session_data = {
        "id": session_id,
        "mode": "assistant",
        "title": "Regression Check",
        "messages": [],
        "chat_history": []
    }

    controller = MagicMock()
    controller.current_session = session_data
    controller.chat_history = []
    controller.active_character = None
    controller.current_mode = "assistant"
    controller._pending_knowledge_sources = None
    controller._pending_thought_data = None

    controller._sync_turn = ChatFlowController._sync_turn.__get__(controller, type(controller))

    q1 = "Какой модуль в neonroute отвечает за видеопамять?"
    controller._sync_turn(role="user", content="Какой модуль в neonroute отвечает за видеопамять?", replace_last=False)

    thought_1 = {
        "content": "Анализ архитектуры: vram_arbitrator и config.py",
        "duration": 2.1,
        "token_count": 28
    }
    ans_1 = "За видеопамять отвечает модуль vram_arbitrator."
    controller._sync_turn(
        role="assistant",
        content=ans_1,
        replace_last=False,
        thought=thought_1,
        knowledge_sources=[{"filename": "architecture.md", "chunk_id": "chunk_0"}]
    )

    session_file = temp_history_env / f"chat_{session_id}.json"
    with open(session_file, "r", encoding="utf-8") as f:
        persisted_json = json.load(f)

    assert len(persisted_json["chat_history"]) == 2, (
        "РЕГРЕССИЯ: chat_history в JSON сохранен пустым ([])."
    )

    reloaded_session = SessionManager.load_session(session_id)
    mounted_chat_history = list(reloaded_session.get("chat_history", []))
    assert len(mounted_chat_history) == 2

    q2 = "А там есть арбитр памяти?"
    mounted_chat_history.append({"role": "user", "content": q2})

    llm_messages = ContextWindowBuilder.get_lm_studio_messages(
        history=mounted_chat_history,
        is_roleplay=False,
        enable_reasoning=True
    )

    assert len(llm_messages) == 4
    assert llm_messages[0]["role"] == "system"
    assert "<think>" in llm_messages[0]["content"]
    assert llm_messages[1]["content"] == q1
    assert "<think>" in llm_messages[2]["content"]
    assert llm_messages[3]["content"] == q2
