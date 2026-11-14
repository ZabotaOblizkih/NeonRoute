"""
Unit tests for MemoryExtractor reasoning tags sanitization, turn limits, and LLM arguments.
"""

from unittest.mock import MagicMock, patch
import pytest

from services.memory_extractor import MemoryExtractor

def test_extractor_triggers_on_two_messages():
    """Тест снижения порога min_messages до 2 (извлечение с 1-го раунда диалога)."""
    history_2 = [
        {"role": "user", "content": "Привет! Меня зовут Роман, я пишу на Python.", "is_user": True},
        {"role": "assistant", "content": "Привет, Роман! Приятно познакомиться.", "is_user": False}
    ]

    mock_client = MagicMock()
    mock_client.chat_completion.return_value = '{"facts": ["Пользователя зовут Роман", "Пользователь пишет на Python"]}'

    mock_mem_db = MagicMock()
    mock_mem_db.add_fact.return_value = 1

    with patch("services.task_queue.get_task_queue", return_value=None), \
         patch("services.memory_extractor.memory_db", mock_mem_db), \
         patch.object(MemoryExtractor, "_run_extraction", wraps=MemoryExtractor._run_extraction) as spy_run:
        # Прямой вызов воркера экстракции
        MemoryExtractor._run_extraction(history_2, mock_client)

    assert mock_client.chat_completion.called
    kwargs = mock_client.chat_completion.call_args[1]
    assert kwargs.get("enable_thinking") is False
    assert kwargs.get("max_tokens") == 512

    assert mock_mem_db.add_fact.call_count == 2
    saved_facts = [call.args[0] for call in mock_mem_db.add_fact.call_args_list]
    assert "Пользователя зовут Роман" in saved_facts
    assert "Пользователь пишет на Python" in saved_facts

def test_extractor_sanitizes_paired_think_tags():
    """Тест очистки парных тегов <think>...</think>."""
    history = [
        {"role": "user", "content": "Я живу в Санкт-Петербурге.", "is_user": True},
        {"role": "assistant", "content": "Отличный город!", "is_user": False}
    ]

    mock_client = MagicMock()
    mock_client.chat_completion.return_value = (
        "<think>\n"
        "Пользователь сообщил, что живет в СПб. Это факт о месте жительства.\n"
        "Сформирую JSON объект.\n"
        "</think>\n"
        '{"facts": ["Пользователь живет в Санкт-Петербурге"]}'
    )

    mock_mem_db = MagicMock()
    with patch("services.memory_extractor.memory_db", mock_mem_db):
        MemoryExtractor._run_extraction(history, mock_client)

    mock_mem_db.add_fact.assert_called_once_with("Пользователь живет в Санкт-Петербурге")

def test_extractor_sanitizes_orphan_closing_think_tag():
    """Тест очистки одиночного закрывающего тега </think> после assistant prefill."""
    history = [
        {"role": "user", "content": "Я вожу мотоцикл.", "is_user": True},
        {"role": "assistant", "content": "Круто!", "is_user": False}
    ]

    mock_client = MagicMock()
    mock_client.chat_completion.return_value = (
        "Рассуждение модели завершено.\n"
        "</think>\n"
        '{"facts": ["Пользователь водит мотоцикл"]}'
    )

    mock_mem_db = MagicMock()
    with patch("services.memory_extractor.memory_db", mock_mem_db):
        MemoryExtractor._run_extraction(history, mock_client)

    mock_mem_db.add_fact.assert_called_once_with("Пользователь водит мотоцикл")

def test_extractor_sanitizes_unclosed_tail_think_tag():
    """Тест очистки незакрытого тега <think> при обрыве генерации по токенам."""
    history = [
        {"role": "user", "content": "Я вегетарианец.", "is_user": True},
        {"role": "assistant", "content": "Поняла!", "is_user": False}
    ]

    # Модель сначала вернула валидный JSON, а потом начала рассуждать и оборвалась
    mock_client = MagicMock()
    mock_client.chat_completion.return_value = (
        '{"facts": ["Пользователь является вегетарианцем"]}\n'
        '<think>Анализ завершен, но вот еще мысли...'
    )

    mock_mem_db = MagicMock()
    with patch("services.memory_extractor.memory_db", mock_mem_db):
        MemoryExtractor._run_extraction(history, mock_client)

    mock_mem_db.add_fact.assert_called_once_with("Пользователь является вегетарианцем")
