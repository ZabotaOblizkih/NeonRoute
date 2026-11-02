import pytest
from unittest.mock import MagicMock
from pathlib import Path

from services.tavern_models import CharacterCard as TavernCard
from services.session_manager import SessionManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.task_queue import InferenceTask
import config


@pytest.fixture(autouse=True)
def isolate_session_storage(tmp_path, monkeypatch):
    r"""
    Redirects all history and session writes to pytest's ephemeral tmp_path.
    Guarantees existing production dialogs in C:\NeonRoute\history remain untouched.
    """
    ephemeral_history = tmp_path / "history"
    ephemeral_history.mkdir(parents=True, exist_ok=True)

    if hasattr(config, "HISTORY_DIR"):
        monkeypatch.setattr(config, "HISTORY_DIR", ephemeral_history)

    monkeypatch.setattr(SessionManager, "SESSIONS_DIR", ephemeral_history, raising=False)

    yield ephemeral_history


@pytest.fixture
def mock_isolated_card():
    """In-memory character card without touching files on disk."""
    return TavernCard(
        name="Кай и Эйла",
        description="Две сестры в рабстве на рынке.",
        personality="Кай дерзкая и защищающая, Эйла пугливая.",
        scenario="Рынок рабов",
        first_mes="*Рынок рабов гудит шумом толпы. Они сидят в клетке.*"
    )


def test_rp_turn_pipeline_isolated_smoke(mock_isolated_card, monkeypatch, isolate_session_storage):
    """
    Validates the end-to-end user input pipeline in complete isolation.
    """
    ephemeral_sess_id = "smoke_ephemeral_test_sess_0000"
    user_name = "Дэниэл"
    user_text = "Я подхожу к решетке и осматриваю замок"

    # Confirm isolation
    assert str(isolate_session_storage) in str(config.HISTORY_DIR)

    history = [
        {"role": "assistant", "content": mock_isolated_card.first_mes},
        {"role": "user", "content": user_text}
    ]

    # 1. Mock LanceDB MemoryDB
    mock_search = MagicMock(return_value=["Дэниэл ранее припрятал отмычку"])
    monkeypatch.setattr("services.memory_db.memory_db.search_rp_memories", mock_search)

    # 2. Mock RPCastManager.scan_and_tick
    mock_scan_res = {
        "active_cast": "",  # Excluded per patch
        "recalled_lore": "\n<RecalledLore>\n• [Событие]: Дэниэл ранее припрятал отмычку\n</RecalledLore>\n"
    }
    mock_scan_and_tick = MagicMock(return_value=mock_scan_res)
    monkeypatch.setattr("services.rp_cast_manager.RPCastManager.scan_and_tick", mock_scan_and_tick)

    # -------------------------------------------------------------
    # Step A: Pre-turn Execution (FastEmbed / LanceDB & Cast)
    # -------------------------------------------------------------
    search_hits = mock_search(ephemeral_sess_id, user_text, top_k=3, max_distance=0.35)
    assert len(search_hits) == 1
    assert "припрятал отмычку" in search_hits[0]

    scan_out = mock_scan_and_tick(ephemeral_sess_id, user_text)
    assert scan_out["active_cast"] == ""
    assert "<RecalledLore>" in scan_out["recalled_lore"]

    # -------------------------------------------------------------
    # Step B: Assemble Messages via SessionManager
    # -------------------------------------------------------------
    messages = SessionManager.get_lm_studio_messages(
        history=history,
        is_roleplay=True,
        character_card=mock_isolated_card,
        user_name=user_name,
        history_limit=65,
        session_id=ephemeral_sess_id,
        active_cast_block=scan_out["active_cast"],
        recalled_lore_block=scan_out["recalled_lore"]
    )

    # -------------------------------------------------------------
    # Step C: Inject Depth-1 Anchor
    # -------------------------------------------------------------
    depth_anchor = RoleplayPromptBuilder.build_depth_anchor(
        mock_isolated_card,
        user_name=user_name,
        user_query=user_text,
        custom_anchor=None
    )
    assert depth_anchor is not None
    assert "кинематографичное" in depth_anchor

    for idx in reversed(range(len(messages))):
        if messages[idx].get("role") == "user":
            anchor_msg = {"role": "system", "content": depth_anchor.strip()}
            messages.insert(idx, anchor_msg)
            break

    # -------------------------------------------------------------
    # Step D: Structure & Prompt Hygiene Verification
    # -------------------------------------------------------------
    assert len(messages) >= 4

    sys_content = messages[0]["content"]
    assert "[Current Active Companions" not in sys_content
    assert "<ActiveCast>" not in sys_content
    assert "<RecalledLore>" in sys_content
    assert "припрятал отмычку" in sys_content

    assert messages[-2]["role"] == "system"
    assert messages[-2]["content"] == depth_anchor.strip()

    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == user_text

    # -------------------------------------------------------------
    # Step E: Enqueue & Execute Mock LLM Task
    # -------------------------------------------------------------
    mock_llm_client = MagicMock()
    mock_llm_client.chat_completion = MagicMock(return_value=["*Кай ", "пристально ", "следит за тобой.*"])

    llm_task = InferenceTask(
        name="LLM Ответ",
        task_type="llm",
        priority=0,
        func=mock_llm_client.chat_completion,
        kwargs={"messages": messages, "temperature": 0.65}
    )

    llm_res = llm_task.func(**llm_task.kwargs)
    assert "".join(llm_res) == "*Кай пристально следит за тобой.*"
    mock_llm_client.chat_completion.assert_called_once_with(messages=messages, temperature=0.65)

    # -------------------------------------------------------------
    # Step F: Post-Turn Background Analysis Dispatch
    # -------------------------------------------------------------
    mock_extractor = MagicMock(return_value={"scene_ambience": "iron bars and whispers"})
    bg_task = InferenceTask(
        name="🎭 Комплексный анализ сцены",
        task_type="memory",
        priority=20,
        func=mock_extractor,
        kwargs={"session_id": ephemeral_sess_id}
    )

    assert bg_task.priority == 20
    assert bg_task.name == "🎭 Комплексный анализ сцены"
    bg_out = bg_task.func(**bg_task.kwargs)
    assert bg_out["scene_ambience"] == "iron bars and whispers"

    # Leak detector: verify no files leaked to production history
    prod_history = Path(r"C:\NeonRoute\history")
    if prod_history.exists():
        leaked_files = list(prod_history.glob(f"*{ephemeral_sess_id}*"))
        assert len(leaked_files) == 0, f"Leak detected in production history: {leaked_files}"
