"""
Тесты протокола рассуждений (Шаг 3):
- Селективный протокол рассуждений [reasoning_protocol] в AssistantPromptBuilder
- Санитизация истории диалога от тегов <think> в ContextWindowBuilder
- Сохранение токенов контекстного окна LLM
"""

import pytest
from services.assistant_prompt_builder import AssistantPromptBuilder, REASONING_PROTOCOL
from services.context_window_builder import ContextWindowBuilder
from services.token_budget_manager import TokenBudgetManager
from services.session_manager import SessionManager


def test_reasoning_protocol_injected_with_knowledge_block():
    """Проверка появления [reasoning_protocol] в системном промпте при наличии базы знаний."""
    kb_block = "<KnowledgeBase>\n<Document name=\"spec.txt\">\nДанные о товаре\n</Document>\n</KnowledgeBase>"
    prompt = AssistantPromptBuilder.build(
        user_name="Алексей",
        knowledge_block=kb_block
    )
    assert "[reasoning_protocol]" in prompt
    assert "<think>...</think>" in prompt
    assert "<KnowledgeBase>" in prompt
    assert "Перед формированием ответа выполни внутренний анализ фактов" in prompt


def test_reasoning_protocol_injected_with_matched_knowledge_docs():
    """Проверка появления [reasoning_protocol] при передаче matched_knowledge_docs."""
    docs = [
        {"filename": "manual.pdf", "content_snippet": "Инструкция по настройке протокола"}
    ]
    prompt = AssistantPromptBuilder.build(
        user_name="Алексей",
        matched_knowledge_docs=docs
    )
    assert "[reasoning_protocol]" in prompt
    assert "<KnowledgeBase>" in prompt


def test_reasoning_protocol_injected_with_enable_flag():
    """Проверка появления [reasoning_protocol] при явном флаге enable_reasoning=True без базы знаний."""
    prompt = AssistantPromptBuilder.build(
        user_name="Алексей",
        enable_reasoning=True
    )
    assert "[reasoning_protocol]" in prompt
    assert "<think>...</think>" in prompt


def test_reasoning_protocol_absent_in_standard_dialogue():
    """Проверка отсутствия [reasoning_protocol] при стандартном диалоге (чистый промпт)."""
    prompt = AssistantPromptBuilder.build(
        user_name="Алексей",
        enable_reasoning=False,
        knowledge_block=None,
        matched_knowledge_docs=None
    )
    assert "[reasoning_protocol]" not in prompt
    assert "<think>" not in prompt
    assert "</think>" not in prompt


def test_context_window_builder_system_prompt_reasoning_flag():
    """Проверка поддержки enable_reasoning в ContextWindowBuilder.build_system_prompt."""
    # Без рассуждений
    prompt_std = ContextWindowBuilder.build_system_prompt(
        is_roleplay=False,
        user_name="Алексей",
        enable_reasoning=False
    )
    assert "[reasoning_protocol]" not in prompt_std

    # С рассуждениями
    prompt_reasoning = ContextWindowBuilder.build_system_prompt(
        is_roleplay=False,
        user_name="Алексей",
        enable_reasoning=True
    )
    assert "[reasoning_protocol]" in prompt_reasoning


def test_prepare_history_for_llm_cleans_think_tags():
    """Проверка очистки старых тегов <think> из истории сообщений ассистента."""
    history = [
        {"role": "user", "content": "Какой артикул у детали?"},
        {
            "role": "assistant",
            "content": "<think>\n1. Сопоставляю вопрос с базой знаний.\n2. Нахожу артикул 12345.\n</think>\nАртикул детали: 12345."
        },
        {"role": "user", "content": "А сколько штук на складе?"}
    ]

    sanitized = ContextWindowBuilder.prepare_history_for_llm(history)
    assert len(sanitized) == 3
    assistant_msg = sanitized[1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == "Артикул детали: 12345."
    assert "<think>" not in assistant_msg["content"]
    assert "</think>" not in assistant_msg["content"]


def test_prepare_history_for_llm_does_not_mix_thought_dict():
    """Проверка: если у ассистента сохранен словарь 'thought', он не подмешивается в текст для LLM."""
    history = [
        {"role": "user", "content": "Привет"},
        {
            "role": "assistant",
            "content": "Привет! Чем могу помочь?",
            "thought": {
                "content": "Внутренний анализ намерений пользователя",
                "duration": 1.25,
                "token_count": 30
            }
        }
    ]

    sanitized = ContextWindowBuilder.prepare_history_for_llm(history)
    assert len(sanitized) == 2
    assistant_msg = sanitized[1]
    assert assistant_msg["content"] == "Привет! Чем могу помочь?"
    assert "Внутренний анализ" not in assistant_msg["content"]
    assert "thought" not in assistant_msg


def test_build_context_messages_alias_and_integration():
    """Проверка функции build_context_messages как псевдонима/метода сборки контекста."""
    history = [
        {"role": "user", "content": "Вопрос"},
        {"role": "assistant", "content": "<think>размышления...</think>Чистый ответ"}
    ]
    res = ContextWindowBuilder.build_context_messages(history)
    assert len(res) == 2
    assert res[1]["content"] == "Чистый ответ"

    # Также через SessionManager
    res_sm = SessionManager.build_context_messages(history)
    assert len(res_sm) == 2
    assert res_sm[1]["content"] == "Чистый ответ"


def test_token_savings_from_think_sanitization():
    """Проверка экономии токенов контекста при санитизации <think>."""
    thought_text = " ".join([f"анализ_шаг_{i}_сверяем_документ" for i in range(150)])
    raw_content = f"<think>{thought_text}</think>Итоговый ответ на 10 токенов."

    history = [
        {"role": "user", "content": "Сложный вопрос"},
        {"role": "assistant", "content": raw_content}
    ]

    raw_tokens = TokenBudgetManager.estimate_message_tokens({"role": "assistant", "content": raw_content})
    sanitized_history = ContextWindowBuilder.prepare_history_for_llm(history)
    sanitized_tokens = TokenBudgetManager.estimate_message_tokens(sanitized_history[1])

    # Токены после очистки должны быть существенно меньше
    assert sanitized_tokens < raw_tokens
    assert (raw_tokens - sanitized_tokens) > 100
    assert sanitized_history[1]["content"] == "Итоговый ответ на 10 токенов."


def test_get_lm_studio_messages_end_to_end_sanitization():
    """Проверка полного цикла get_lm_studio_messages с санитизацией мыслей и передачей enable_reasoning."""
    history = [
        {"role": "user", "content": "Первый вопрос"},
        {"role": "assistant", "content": "<think>думаем над ответом 1</think>Ответ 1"},
        {"role": "user", "content": "Второй вопрос по документу"}
    ]

    matched_docs = [{"filename": "spec.txt", "content_snippet": "Спецификация"}]

    messages = SessionManager.get_lm_studio_messages(
        history=history,
        is_roleplay=False,
        matched_knowledge_docs=matched_docs,
        enable_reasoning=True
    )

    # Проверяем системный промпт
    sys_msg = messages[0]
    assert sys_msg["role"] == "system"
    assert "[reasoning_protocol]" in sys_msg["content"]
    assert "<KnowledgeBase>" in sys_msg["content"]

    # Проверяем историю: предыдущий ответ ассистента очищен
    assistant_hist = [m for m in messages if m.get("role") == "assistant"]
    assert len(assistant_hist) == 1
    assert assistant_hist[0]["content"] == "Ответ 1"
    assert "<think>" not in assistant_hist[0]["content"]
