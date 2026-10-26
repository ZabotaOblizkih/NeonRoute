"""
Unit tests for Knowledge CoT (Chain-of-Thought) verification and ThoughtStreamParser tag flexibility.
1. build_knowledge_context() contains <KnowledgeBase> XML and doc contents for non-empty docs, without reasoning protocols.
2. build_knowledge_context() returns empty string for empty doc lists.
3. AssistantPromptBuilder.build() injects ROUTING_TAIL_PROTOCOL with <think> when assembling the prompt.
4. ThoughtStreamParser supports both <think>...</think> and <thought>...</thought> (case-insensitive)
   with boundary chunk stitching, emitting start_think, thought_chunk, end_think, normal_chunk.
"""

import pytest
from services.assistant_prompt_builder import AssistantPromptBuilder, ROUTING_TAIL_PROTOCOL, REASONING_PROTOCOL
from services.thought_stream_parser import ThoughtStreamParser, ThinkParserState


def test_build_knowledge_context_non_empty():
    """Проверка, что build_knowledge_context(docs) при непустом списке содержит исключительно XML-блок <KnowledgeBase>."""
    docs = [
        {
            "filename": "specs.txt",
            "chunk_id": "specs.txt#c1",
            "content": "Артикул 123-ABC, напряжение 220V."
        },
        {
            "filename": "manual.pdf",
            "chunk_id": "manual.pdf#c0",
            "content": "Инструкция по монтажу оборудования."
        }
    ]

    result = AssistantPromptBuilder.build_knowledge_context(docs)

    # 1. Наличие XML-блока базы знаний и содержимого
    assert "<KnowledgeBase>" in result
    assert "</KnowledgeBase>" in result
    assert 'name="specs.txt"' in result
    assert "Артикул 123-ABC" in result
    assert 'name="manual.pdf"' in result
    assert "Инструкция по монтажу" in result

    # 2. build_knowledge_context не содержит протокол рассуждений (он добавляется в AssistantPromptBuilder.build)
    assert "[СТРОГИЙ ПОРЯДОК ГЕНЕРАЦИИ]" not in result
    assert "<think>" not in result

    # 3. При общей сборке через AssistantPromptBuilder.build протокол генерации инжектируется
    full_prompt = AssistantPromptBuilder.build(matched_knowledge_docs=docs)
    assert "<KnowledgeBase>" in full_prompt
    assert "[СТРОГИЙ ПОРЯДОК ГЕНЕРАЦИИ]" in full_prompt
    assert "<think>" in full_prompt


def test_build_knowledge_context_empty():
    """Проверка, что при пустом списке возвращается пустая строка ""."""
    assert AssistantPromptBuilder.build_knowledge_context([]) == ""
    assert AssistantPromptBuilder.build_knowledge_context(None) == ""
    # Документы без текстового содержимого
    empty_docs = [{"filename": "empty.txt", "content": ""}]
    assert AssistantPromptBuilder.build_knowledge_context(empty_docs) == ""


def test_thought_stream_parser_with_think_tags():
    """Проверка корректного перехвата потока с тегами <think>...</think>."""
    parser = ThoughtStreamParser()
    chunks = [
        "Привет! <think>",
        "Проверяю артикул 123-ABC в базе знаний. ",
        "Данные подтверждены.",
        "</think>",
        "Артикул 123-ABC в наличии."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    ev_types = [e[0] for e in events]
    assert "start_think" in ev_types
    assert "thought_chunk" in ev_types
    assert "end_think" in ev_types
    assert "normal_chunk" in ev_types

    assert parser.state == ThinkParserState.THINK_FINISHED
    assert "Проверяю артикул 123-ABC" in parser.thought_text
    assert "Артикул 123-ABC в наличии." in parser.normal_text
    assert "<think>" not in parser.normal_text
    assert "</think>" not in parser.normal_text


def test_thought_stream_parser_with_thought_tags():
    """Проверка корректного перехвата потока с альтернативными тегами <thought>...</thought>."""
    parser = ThoughtStreamParser()
    chunks = [
        "Добрый день! <thought>",
        "Аудит фактов: сверяю параметры модели с документацией. ",
        "Отклонений не обнаружено.",
        "</thought>",
        "Параметры полностью соответствуют спецификации."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    ev_types = [e[0] for e in events]
    assert "start_think" in ev_types
    assert "thought_chunk" in ev_types
    assert "end_think" in ev_types
    assert "normal_chunk" in ev_types

    assert parser.state == ThinkParserState.THINK_FINISHED
    assert "Аудит фактов: сверяю параметры" in parser.thought_text
    assert "Параметры полностью соответствуют" in parser.normal_text
    assert "<thought>" not in parser.normal_text
    assert "</thought>" not in parser.normal_text


def test_thought_stream_parser_case_insensitive_and_mixed():
    """Проверка регистронезависимости тегов <THINK> / <THOUGHT>."""
    # Верхний регистр <THOUGHT>...</THOUGHT>
    p1 = ThoughtStreamParser()
    ev1 = p1.feed("Ответ: <THOUGHT>Мысли в верхнем регистре</THOUGHT>Чистовой текст.")
    ev1 += p1.finalize()
    assert "Мысли в верхнем регистре" in p1.thought_text
    assert "Ответ: Чистовой текст." in p1.normal_text
    assert p1.state == ThinkParserState.THINK_FINISHED

    # Смешанный регистр открывающего и закрывающего тега <Think>...</think>
    p2 = ThoughtStreamParser()
    ev2 = p2.feed("<Think>Размышления</Think>Результат.")
    ev2 += p2.finalize()
    assert p2.thought_text == "Размышления"
    assert p2.normal_text == "Результат."
    assert p2.state == ThinkParserState.THINK_FINISHED


def test_thought_stream_parser_split_thought_boundary():
    """Проверка буферизации разрыва тега <thought> и </thought> на границах чанков."""
    parser = ThoughtStreamParser()
    chunks = [
        "Преамбула. <thou",
        "ght>Скрытые мысли на стыке сетевых пакетов.",
        " Анализирую контекст.</thou",
        "ght>Финал сообщения."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    assert parser.state == ThinkParserState.THINK_FINISHED
    assert parser.thought_text == "Скрытые мысли на стыке сетевых пакетов. Анализирую контекст."
    assert parser.normal_text == "Преамбула. Финал сообщения."

    thought_data = parser.get_thought_data()
    assert thought_data is not None
    assert thought_data["content"] == "Скрытые мысли на стыке сетевых пакетов. Анализирую контекст."
    assert thought_data["token_count"] > 0
