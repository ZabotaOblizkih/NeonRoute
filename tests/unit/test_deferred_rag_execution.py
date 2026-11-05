import pytest
from unittest.mock import MagicMock, patch
from services.task_queue import InferenceTask
from ui.controllers.assistant_flow_controller import run_assistant_inference_deferred


def test_deferred_rag_execution():
    """
    Проверяет отложенную сборку контекста (Deferred RAG Execution):
    1. При создании InferenceTask методы RAG НЕ вызываются.
    2. В момент исполнения задачи воркером вызываются KnowledgeManager и MemoryDB.
    3. Вызов lm_client.chat_completion получает обогащенные messages.
    4. При передаче готовых messages (Legacy-режим) RAG не вызывается повторно.
    """
    mock_find_docs = MagicMock(return_value=[
        {
            "filename": "quantum_physics.md",
            "content_snippet": "Квантовый компьютер использует кубиты для суперпозиции.",
            "score": 0.95
        }
    ])
    mock_search_facts = MagicMock(return_value=[
        "Пользователь изучает квантовую теорию"
    ])

    mock_lm_client = MagicMock()
    mock_lm_client.chat_completion.return_value = "Квантовые компьютеры работают на принципах квантовой механики."

    with patch("services.knowledge_manager.KnowledgeManager.find_relevant_documents", mock_find_docs), \
         patch("services.memory_db.memory_db.search_relevant_facts", mock_search_facts):

        # 2. Создаем задачу с отложенной сборкой (без messages)
        task = InferenceTask(
            name="LLM Ответ (Deferred)",
            task_type="llm",
            func=run_assistant_inference_deferred,
            kwargs={
                "prompt": "Как устроен квантовый процессор?",
                "session_id": "test_session_deferred_01",
                "messages": None,
                "lm_client": mock_lm_client,
                "temperature": 0.7
            }
        )

        # RAG не должен вызываться в момент создания задачи
        assert mock_find_docs.call_count == 0
        assert mock_search_facts.call_count == 0

        # 3. Исполняем задачу (симуляция работы воркера TaskQueueWorker)
        result = task.func(**task.kwargs)
        assert result == "Квантовые компьютеры работают на принципах квантовой механики."

        # 4. Проверяем, что RAG вызвался именно во время выполнения задачи
        assert mock_find_docs.call_count == 1
        assert mock_search_facts.call_count == 1

        # Проверяем, что lm_client.chat_completion получил обогащенные messages
        assert mock_lm_client.chat_completion.call_count == 1
        call_kwargs = mock_lm_client.chat_completion.call_args[1]
        sent_messages = call_kwargs["messages"]
        assert isinstance(sent_messages, list)
        assert len(sent_messages) >= 2
        # Системное сообщение должно содержать знания RAG
        sys_msg = sent_messages[0]["content"]
        assert "Квантовый компьютер использует кубиты" in sys_msg or "<KnowledgeBase>" in sys_msg

        # 5. Проверка Legacy-режима: при передаче готовых messages повторный вызов RAG не происходит
        mock_find_docs.reset_mock()
        mock_search_facts.reset_mock()
        mock_lm_client.chat_completion.reset_mock()

        legacy_messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello world"}
        ]
        legacy_task = InferenceTask(
            name="LLM Ответ (Legacy)",
            task_type="llm",
            func=run_assistant_inference_deferred,
            kwargs={
                "prompt": "Hello world",
                "session_id": "test_session_legacy_01",
                "messages": legacy_messages,
                "lm_client": mock_lm_client,
                "temperature": 0.5
            }
        )

        legacy_res = legacy_task.func(**legacy_task.kwargs)
        # RAG методы НЕ должны вызываться
        assert mock_find_docs.call_count == 0
        assert mock_search_facts.call_count == 0
        # chat_completion получил ровно переданный список messages
        assert mock_lm_client.chat_completion.call_count == 1
        assert mock_lm_client.chat_completion.call_args[1]["messages"] == legacy_messages
