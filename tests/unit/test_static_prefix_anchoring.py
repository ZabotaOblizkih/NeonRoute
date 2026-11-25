"""
Unit tests for Static Prefix Anchoring and KV-Cache Telemetry:
1. Invariant bit-exact stability of messages[0]["content"] (system prompt) across time changes,
   RAG facts, and skill switches, plus presence of the <TurnContext> role-guard instruction.
2. Injection of <TurnContext> exclusively into the ephemeral last user message payload
   without polluting the persistent session history.
3. LMStudioClient inclusion of id_slot: 0, stream_options: {"include_usage": True},
   and parsing of final SSE chunk with timings (cache_n, prompt_n).
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from services.assistant_prompt_builder import AssistantPromptBuilder, SYSTEM_PERSONA_TEMPLATE
from services.context_window_builder import ContextWindowBuilder
from services.lm_studio import LMStudioClient


# =========================================================================
# 1. Побитовая идентичность messages[0]["content"] (Static Prefix)
# =========================================================================

def test_system_prompt_prefix_bit_exact_stability_across_time_rag_and_skills():
    """
    Проверяет, что messages[0]['content'] (системный промпт) побитово идентичен
    при:
    - смене минуты на часах (dt_turn1 != dt_turn2);
    - изменении динамических фактов памяти LanceDB RAG (facts_1 != facts_2);
    - смене активного скилла ('gallery' -> 'default').
    Также проверяет наличие статичной защитной инструкции про <TurnContext>.
    """
    assert "- Служебный блок <TurnContext>...</TurnContext>" in SYSTEM_PERSONA_TEMPLATE
    assert "- Дата и время: {current_dt_str}" not in SYSTEM_PERSONA_TEMPLATE

    dt_1 = datetime(2026, 11, 18, 23, 10, 0)
    dt_2 = datetime(2026, 11, 18, 23, 15, 0)

    history_1 = [{"role": "user", "content": "Нарисуй неоновый город в стиле киберпанк"}]
    history_2 = [{"role": "user", "content": "Спасибо! А теперь расскажи про погоду"}]

    # Ход 1: скилл 'gallery', факты RAG про киберпанк, время 23:10
    messages_1 = ContextWindowBuilder.get_lm_studio_messages(
        history=history_1,
        mode="assistant",
        user_name="Виктор",
        relevant_facts=["Любит киберпанк и неон", "RTX 5050 8GB"],
        active_assistant_skill="gallery",
        selected_forge_checkpoint="ponyDiffusionV6XL.safetensors",
        dt=dt_1,
    )

    # Ход 2: дефолтный диалог (нет скилла), другие факты RAG, время 23:15
    messages_2 = ContextWindowBuilder.get_lm_studio_messages(
        history=history_2,
        mode="assistant",
        user_name="Виктор",
        relevant_facts=["Живет в Санкт-Петербурге", "Интересуется метеорологией"],
        active_assistant_skill=None,
        selected_forge_checkpoint=None,
        dt=dt_2,
    )

    sys_1 = messages_1[0]["content"]
    sys_2 = messages_2[0]["content"]

    # КРИТИЧЕСКИЙ ИНВАРИАНТ: побитовая идентичность префикса messages[0]
    assert sys_1 == sys_2
    assert "<TurnContext>" in sys_1  # Инструкция для LLM присутствует в системном ядре
    assert "23:10" not in sys_1      # Время удалено из системного промпта
    assert "23:15" not in sys_2
    assert "Любит киберпанк" not in sys_1  # Волатильные факты удалены из системного промпта
    assert "[SYSTEM DIRECTIVE:" not in sys_1  # Директива скилла удалена из системного промпта


# =========================================================================
# 2. Изоляция <TurnContext> в последнем сообщении без порчи истории
# =========================================================================

def test_turn_context_injection_only_in_ephemeral_payload_not_history():
    """
    Проверяет, что блок <TurnContext>...</TurnContext> прикрепляется ТОЛЬКО к временной копии
    последнего сообщения пользователя messages[-1]['content'], а исходная история
    остается нетронутой.
    """
    raw_user_text = "Нарисуй футуристический автомобиль"
    original_history = [
        {"role": "user", "content": "Привет, Элария!"},
        {"role": "assistant", "content": "Привет! Чем могу помочь?"},
        {"role": "user", "content": raw_user_text}
    ]

    # Делаем глубокую копию для сверки после вызова
    history_snapshot = [dict(m) for m in original_history]

    messages = ContextWindowBuilder.get_lm_studio_messages(
        history=original_history,
        mode="assistant",
        user_name="Виктор",
        relevant_facts=["Любит спорткары"],
        active_assistant_skill="gallery",
        selected_forge_checkpoint="ponyDiffusionV6XL.safetensors",
        dt=datetime(2026, 11, 18, 23, 20, 0)
    )

    # 1. Проверяем, что исходная история НЕ была мутирована
    assert original_history == history_snapshot
    assert original_history[-1]["content"] == raw_user_text
    assert "<TurnContext>" not in original_history[-1]["content"]

    # 2. Проверяем, что в выходном messages[-1] присутствует <TurnContext>
    last_msg = messages[-1]
    assert last_msg["role"] == "user"
    assert last_msg["content"].startswith("<TurnContext>")
    assert "</TurnContext>" in last_msg["content"]
    assert raw_user_text in last_msg["content"]
    assert "- Текущая дата и время: 18.11.2026" in last_msg["content"]
    assert "Любит спорткары" in last_msg["content"]
    assert "[SYSTEM DIRECTIVE: SDXL Image Prompt Engineer]" in last_msg["content"]

    # 3. Промежуточные сообщения истории не содержат TurnContext
    for m in messages[1:-1]:
        assert "<TurnContext>" not in m["content"]


# =========================================================================
# 3. LMStudioClient: id_slot: 0, stream_options и парсинг timings
# =========================================================================

def test_lm_studio_client_passes_id_slot_and_stream_options():
    """
    Проверяет, что при работе с headless llama-server передаются 'id_slot': 0
    и 'stream_options': {'include_usage': True}.
    """
    pm_mock = MagicMock()
    pm_mock.has_headless_server.return_value = True

    client = LMStudioClient()
    client.process_manager = pm_mock
    client._last_loaded_model_id = "test-model"
    client.is_model_loaded = MagicMock(return_value=True)

    captured_payload = {}

    def mock_post(url, *args, **kwargs):
        nonlocal captured_payload
        captured_payload = kwargs.get("json", {})
        resp = MagicMock(status_code=200)
        resp.iter_lines.return_value = [
            b'data: {"choices": [{"delta": {"content": "ok"}}]}',
            b'data: [DONE]'
        ]
        return resp

    with patch.object(client._persistent_session, "post", side_effect=mock_post):
        client.chat_completion(
            messages=[{"role": "user", "content": "тест"}],
            stream=True
        )

    assert captured_payload.get("id_slot") == 0
    assert captured_payload.get("stream_options") == {"include_usage": True}
    assert captured_payload.get("cache_prompt") is True


def test_lm_studio_client_parses_final_sse_chunk_with_timings_and_usage(caplog):
    """
    Проверяет, что LMStudioClient корректно парсит финальный служебный SSE-чанк
    {"choices": [], "usage": {"prompt_tokens": 1500}, "timings": {"cache_n": 1420, "prompt_n": 80}}
    и выводит реальную статистику в лог [LMS-TELEMETRY].
    """
    import logging
    pm_mock = MagicMock()
    pm_mock.has_headless_server.return_value = True

    client = LMStudioClient()
    client.process_manager = pm_mock
    client._last_loaded_model_id = "test-model"
    client.is_model_loaded = MagicMock(return_value=True)

    final_service_chunk = json.dumps({
        "choices": [],
        "usage": {
            "prompt_tokens": 1500,
            "completion_tokens": 30,
            "total_tokens": 1530
        },
        "timings": {
            "cache_n": 1420,
            "prompt_n": 80,
            "prompt_ms": 250.0,
            "predicted_n": 30,
            "predicted_ms": 600.0
        }
    })

    sse_lines = [
        b'data: {"choices": [{"delta": {"content": "\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"}}]}',  # "Привет"
        f"data: {final_service_chunk}".encode("utf-8"),
        b"data: [DONE]"
    ]

    mock_resp = MagicMock(status_code=200)
    mock_resp.iter_lines.return_value = sse_lines

    with patch.object(client._persistent_session, "post", return_value=mock_resp):
        with caplog.at_level(logging.INFO):
            res = client.chat_completion(
                messages=[{"role": "user", "content": "тест"}],
                stream=True
            )

    assert "Привет" in res
    telemetry_logs = [record.message for record in caplog.records if "[LMS-TELEMETRY]" in record.message and "Готово:" in record.message]
    assert len(telemetry_logs) >= 1
    t_log = telemetry_logs[0]

    assert "prompt_tokens=1500" in t_log
    assert "KV hit: 1420 tok" in t_log
    assert "eval: 80 tok" in t_log
