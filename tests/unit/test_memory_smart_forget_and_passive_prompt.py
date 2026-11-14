"""
Unit tests for Smart Memory Forget ("Забудь ..."), Semantic Fallback,
Suppression of MemoryExtractor on memory commands, and Softened <PassiveMemory> Prompt.
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile
import sqlite3

from services.memory_db import (
    MemoryDB,
    clean_forget_query,
    handle_memory_command,
    _DELETE_KEYWORD_REGEX
)
from services.memory_extractor import MemoryExtractor
from services.assistant_flow import sanitize_messages_for_memory
from services.assistant_prompt_builder import (
    AssistantPromptBuilder,
    SYSTEM_PERSONA_TEMPLATE
)


def test_clean_forget_query_variations():
    """Проверяет очистку речевых связок и нормализацию 'ё' -> 'е'."""
    assert clean_forget_query("о том, что я пью черный кофе") == "пью черный кофе"
    assert clean_forget_query("о том, что я пью чёрный кофе") == "пью черный кофе"
    assert clean_forget_query("что я пью черный кофе") == "пью черный кофе"
    assert clean_forget_query("про черный кофе") == "черный кофе"
    assert clean_forget_query("про чёрный кофе") == "черный кофе"
    assert clean_forget_query("удали из памяти о том, что я люблю джаз") == "люблю джаз"
    assert clean_forget_query("сотри из памяти, что я живу в Самаре") == "живу в Самаре"
    assert clean_forget_query("факт о том, что я программист") == "программист"
    assert clean_forget_query("что я") == ""


def test_smart_forget_exact_and_yo_normalization():
    """
    Проверяет точное удаление по подстроке с нормализацией 'ё' -> 'е'
    (в БД 'Пользователь пьёт чёрный кофе', запрос 'Забудь про черный кофе').
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_mem.db"
        mem_db = MemoryDB(db_path=db_path)
        try:
            with mem_db._get_connection() as conn:
                conn.execute(
                    "INSERT INTO user_memory (id, fact, created_at) VALUES (1, ?, datetime('now'));",
                    ("Пользователь пьёт чёрный кофе",)
                )
                conn.commit()

            # Мокаем LanceDB движок, чтобы не трогать векторную таблицу
            with patch("services.memory_db._get_embedding_engine", return_value=(MagicMock(), MagicMock(), MagicMock())):
                handled, reply = handle_memory_command("Забудь про черный кофе", db=mem_db)

            assert handled is True
            assert "Забыла: «Пользователь пьёт чёрный кофе»." in reply

            # Проверяем удаление из SQLite
            assert len(mem_db.get_all_facts()) == 0
        finally:
            mem_db.close()


def test_smart_forget_semantic_fallback_and_exact_text_reply():
    """
    Проверяет семантический фоллбэк:
    В БД факт: 'Пользователь пьёт чёрный кофе' (форма 'пьёт').
    Запрос: 'Забудь о том, что я пью черный кофе' (форма 'пью').
    Точный поиск по подстроке не находит 'пью' в 'пьёт', срабатывает LanceDB fallback.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_mem.db"
        mem_db = MemoryDB(db_path=db_path)
        try:
            with mem_db._get_connection() as conn:
                conn.execute(
                    "INSERT INTO user_memory (id, fact, created_at) VALUES (42, ?, datetime('now'));",
                    ("Пользователь пьёт чёрный кофе",)
                )
                conn.commit()

            # Семантический поиск находит релевантный факт с дистанцией <= 0.45
            with patch.object(mem_db, "search_relevant_facts", return_value=["Пользователь пьёт чёрный кофе"]) as mock_search:
                with patch("services.memory_db._get_embedding_engine", return_value=(MagicMock(), MagicMock(), MagicMock())):
                    handled, reply = handle_memory_command("Забудь о том, что я пью черный кофе", db=mem_db)

            assert handled is True
            assert mock_search.called
            assert mock_search.call_args[1].get("max_distance") == 0.45
            assert reply == "Забыла: «Пользователь пьёт чёрный кофе»."
            assert len(mem_db.get_all_facts()) == 0
        finally:
            mem_db.close()


def test_smart_forget_not_found_returns_clean_message():
    """Проверяет ответ, если ни по подстроке, ни семантически факт не найден."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_mem.db"
        mem_db = MemoryDB(db_path=db_path)
        try:
            with patch.object(mem_db, "search_relevant_facts", return_value=[]):
                handled, reply = handle_memory_command("Забудь про зеленый чай", db=mem_db)

            assert handled is True
            assert "Не нашла в памяти записей, связанных с «зеленый чай»." in reply
        finally:
            mem_db.close()


def test_memory_command_suppresses_background_memory_extractor():
    """
    Проверяет, что для реплик управления памятью ('Забудь...', 'Запомни...', 'Очисти память')
    НЕ вызывается фоновый MemoryExtractor и они санируются из контекста.
    """
    with patch("services.task_queue.get_task_queue") as mock_get_tq:
        mock_tq = MagicMock()
        mock_get_tq.return_value = mock_tq

        # 1. extract_facts_async с командой 'Забудь ...'
        history_forget = [
            {"role": "user", "content": "Забудь о том, что я пью черный кофе"},
            {"role": "assistant", "content": "Забыла: «Пользователь пьёт чёрный кофе»."}
        ]
        MemoryExtractor.extract_facts_async(history_forget)
        mock_tq.enqueue.assert_not_called()

        # 2. extract_facts_async с командой 'Очисти память'
        history_clear = [
            {"role": "user", "content": "Очисти память"},
            {"role": "assistant", "content": "Очистила всю память о тебе."}
        ]
        MemoryExtractor.extract_facts_async(history_clear)
        mock_tq.enqueue.assert_not_called()

        # 3. extract_facts_async с командой 'Запомни ...'
        history_save = [
            {"role": "user", "content": "Запомни, что я живу в Москве"},
            {"role": "assistant", "content": "Записала в долговременную память. Буду учитывать в ответах."}
        ]
        MemoryExtractor.extract_facts_async(history_save)
        mock_tq.enqueue.assert_not_called()

    # 4. Проверяем sanitize_messages_for_memory:
    # служебные реплики команд памяти и подтверждений исключаются, обычные реплики сохраняются
    mixed_messages = [
        {"role": "user", "content": "Забудь о том, что я пью черный кофе"},
        {"role": "assistant", "content": "Забыла: «Пользователь пьёт чёрный кофе»."},
        {"role": "user", "content": "Какая сегодня погода в Москве?"},
        {"role": "assistant", "content": "В Москве сегодня солнечно, +18°C."}
    ]
    sanitized = sanitize_messages_for_memory(mixed_messages)
    assert len(sanitized) == 2
    assert sanitized[0]["content"] == "Какая сегодня погода в Москве?"
    assert sanitized[1]["content"] == "В Москве сегодня солнечно, +18°C."


def test_passive_memory_softened_prompt_contract():
    """
    Проверяет смягчение директивы <PassiveMemory>:
    вместо жесткого 'только при прямом вопросе о них' — естественное органичное использование.
    """
    # 1. Проверяем SYSTEM_PERSONA_TEMPLATE
    assert "только при прямом вопросе о них" not in SYSTEM_PERSONA_TEMPLATE
    assert "органично помогают дать более точный" in SYSTEM_PERSONA_TEMPLATE
    assert "не навязывай и не перечисляй их без повода" in SYSTEM_PERSONA_TEMPLATE

    # 2. Проверяем build_passive_memory
    facts = ["Пользователь интересуется танком Т-34"]
    mem_block = AssistantPromptBuilder.build_passive_memory(facts)

    assert "<PassiveMemory>" in mem_block
    assert "</PassiveMemory>" in mem_block
    assert "исключительно для ответов на прямые вопросы" not in mem_block
    assert "используй органично" in mem_block
    assert "не перечисляй без повода" in mem_block
    assert "- Пользователь интересуется танком Т-34" in mem_block
