"""
Unit tests for AssistantPromptBuilder and optimized Elaria system prompt:
1. Compact SYSTEM_PERSONA_TEMPLATE with [GALERY], reality boundaries, and no obsolete [engineer_task].
2. KnowledgeBase and ROUTING_TAIL_PROTOCOL placed at the very end (tail) of the system prompt.
3. Fallback and generation protocol structure.
"""

import pytest
from services.assistant_prompt_builder import (
    AssistantPromptBuilder,
    DEFAULT_ELARIA_SYSTEM_PROMPT,
    SYSTEM_PERSONA_TEMPLATE,
    ROUTING_TAIL_PROTOCOL,
    REASONING_PROTOCOL
)


def test_compact_default_elaria_system_prompt():
    """Проверка системного промпта по умолчанию и наличия ключевых директив."""
    # 1. Проверяем сохранение строгих протоколов и отсутствие [engineer_task]
    assert "[engineer_task]" not in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "[/engineer_task]" not in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "протокол визуализации (навык «галерея»):" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "[GALERY]" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "добавь команду вызова навыка: [GALERY]" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "<AttachedDocument>" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "дай компактную выжимку" in DEFAULT_ELARIA_SYSTEM_PROMPT
    assert "границы реальности:" in DEFAULT_ELARIA_SYSTEM_PROMPT

    # 2. Проверяем отсутствие удаленных описаний Tavern/Chub
    assert "tavern/chub" not in DEFAULT_ELARIA_SYSTEM_PROMPT.lower()
    assert "ролевой чат" not in DEFAULT_ELARIA_SYSTEM_PROMPT.lower()


def test_knowledge_base_and_reasoning_at_the_tail():
    """Проверка, что <KnowledgeBase> и ROUTING_TAIL_PROTOCOL располагаются в хвосте системного промпта."""
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
    assert "[СТРОГИЙ ПОРЯДОК ГЕНЕРАЦИИ]" in prompt
    assert "<think>" in prompt

    # Проверяем взаимный порядок:
    # UserInfo -> identity & persona -> KnowledgeBase -> ROUTING_TAIL_PROTOCOL (в хвосте)
    idx_user = prompt.find("<UserInfo>")
    idx_identity = prompt.find("[identity & persona]")
    idx_kb = prompt.find("<KnowledgeBase>")
    idx_tail = prompt.find("[СТРОГИЙ ПОРЯДОК ГЕНЕРАЦИИ]")

    assert idx_user < idx_identity, "UserInfo должен предшествовать identity"
    assert idx_identity < idx_kb, "KnowledgeBase должен быть расположен после identity"
    assert idx_kb < idx_tail, "ROUTING_TAIL_PROTOCOL должен идти после блока KnowledgeBase (в хвосте)"
    assert prompt.endswith(ROUTING_TAIL_PROTOCOL.format(user_name="Иван"))


def test_reasoning_only_at_the_tail():
    """Проверка, что при сборке промпта протокол генерации всегда находится в конце."""
    prompt = AssistantPromptBuilder.build(
        user_name="Иван",
        enable_reasoning=True
    )

    idx_identity = prompt.find("[identity & persona]")
    idx_tail = prompt.find("[СТРОГИЙ ПОРЯДОК ГЕНЕРАЦИИ]")

    assert idx_tail != -1
    assert idx_identity < idx_tail, "ROUTING_TAIL_PROTOCOL должен располагаться в конце промпта"
    assert prompt.endswith(ROUTING_TAIL_PROTOCOL.format(user_name="Иван"))


def test_clean_dialogue_without_kb():
    """Проверка системного сообщения в диалоге без KB (содержит v13 протокол в хвосте)."""
    prompt = AssistantPromptBuilder.build(
        user_name="Иван",
        enable_reasoning=False
    )

    assert "<UserInfo>" in prompt
    assert "[identity & persona]" in prompt
    assert "</KnowledgeBase>" not in prompt
    assert "[СТРОГИЙ ПОРЯДОК ГЕНЕРАЦИИ]" in prompt
    assert prompt.endswith(ROUTING_TAIL_PROTOCOL.format(user_name="Иван"))
