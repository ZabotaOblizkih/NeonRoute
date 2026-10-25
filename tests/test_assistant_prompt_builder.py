"""
Unit tests for AssistantPromptBuilder and optimized Elaria system prompt:
1. Compact DEFAULT_ELARIA_SYSTEM_PROMPT with strict [GALERY] and [engineer_task] rules.
2. KnowledgeBase and [thinking_protocol] placed at the very end (tail) of the system prompt (Recency Bias).
3. Fallback and selective reasoning injection order.
"""

import pytest
from services.assistant_prompt_builder import (
    AssistantPromptBuilder,
    DEFAULT_ELARIA_SYSTEM_PROMPT,
    REASONING_PROTOCOL
)


def test_compact_default_elaria_system_prompt():
    """Проверка компактности системного промпта по умолчанию и наличия ключевых директив."""
    # 1. Проверяем сохранение строгих протоколов
    assert "[engineer_task]" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "[/engineer_task]" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "протокол визуализации (навык «галерея»):" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "[GALERY]" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "добавь команду вызова навыка: [GALERY]" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "<AttachedDocument>" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "дай компактную выжимку" in DEFAULT_ELARIA_SYSTEM_PROMPT

    # 2. Проверяем отсутствие удаленных описаний Tavern/Chub
    assert "tavern/chub" not in DEFAULT_ELARIA_SYSTEM_PROMPT.lower()
    assert "ролевой чат" not in DEFAULT_ELARIA_SYSTEM_PROMPT.lower()


def test_knowledge_base_and_reasoning_at_the_tail():
    """Проверка, что <KnowledgeBase> и [thinking_protocol] располагаются в хвосте системного промпта."""
    docs = [
        {
            "filename": "firmware.txt",
            "chunk_id": "firmware.txt#c0",
            "content": "Версия прошивки 2.4.1 для чипсета STM32F4."
        }
    ]

    prompt = AssistantPromptBuilder.build(
        user_name="Иван",
        matched_knowledge_docs=docs,
        enable_reasoning=True
    )

    # Проверяем наличие всех секций
    assert "<UserInfo>" in prompt
    assert "[identity & persona]" in prompt
    assert "<KnowledgeBase>" in prompt
    assert "[thinking_protocol: верификация источника]" in prompt

    # Проверяем взаимный порядок:
    # UserInfo -> identity & persona -> KnowledgeBase -> thinking_protocol (в хвосте)
    idx_user = prompt.find("<UserInfo>")
    idx_identity = prompt.find("[identity & persona]")
    idx_kb = prompt.find("<KnowledgeBase>")
    idx_think = prompt.find("[thinking_protocol: верификация источника]")

    assert idx_user < idx_identity, "UserInfo должен предшествовать identity"
    assert idx_identity < idx_kb, "KnowledgeBase должен быть расположен после identity (в хвосте)"
    assert idx_kb < idx_think, "thinking_protocol должен идти после блока KnowledgeBase"


def test_reasoning_only_at_the_tail():
    """Проверка, что при enable_reasoning=True без базы знаний протокол также в хвосте."""
    prompt = AssistantPromptBuilder.build(
        user_name="Иван",
        enable_reasoning=True
    )

    idx_identity = prompt.find("[identity & persona]")
    idx_think = prompt.find("[thinking_protocol: верификация источника]")

    assert idx_think != -1
    assert idx_identity < idx_think, "thinking_protocol должен располагаться в конце промпта"


def test_clean_dialogue_without_kb_or_reasoning():
    """Проверка системного сообщения в чистом диалоге без KB и рассуждений."""
    prompt = AssistantPromptBuilder.build(
        user_name="Иван",
        enable_reasoning=False
    )

    assert "<UserInfo>" in prompt
    assert "[identity & persona]" in prompt
    assert "<KnowledgeBase>" not in prompt
    assert "[thinking_protocol" not in prompt
