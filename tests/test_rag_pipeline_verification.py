"""
tests/test_rag_pipeline_verification.py

Регрессионный тестовый набор для валидации исправлений RAG-конвейера:
- Токенизация и морфология (SearchTokenizer)
- Структурный чанкинг и разбиение сверхдлинных блоков кода (MarkdownChunker)
- Адаптивный контекстный бюджет (KnowledgeManager)
- Сквозное обнаружение системных файлов и компонентов архитектуры
"""

import pytest
from pathlib import Path
from typing import List, Dict, Any

from services.search_tokenizer import SearchTokenizer
from services.markdown_chunker import MarkdownChunker
from services.hybrid_search import BM25Index, HybridSearchEngine
from services.knowledge_manager import KnowledgeManager


class TestSearchTokenizerMorphology:
    """Проверка морфологии, стемминга и разбора составных терминов."""

    def test_russian_stemming_matches_different_cases(self):
        """Словоформы 'память', 'памяти', 'памятью' должны давать единую основу."""
        tokens_nom = SearchTokenizer.tokenize("память")
        tokens_gen = SearchTokenizer.tokenize("памяти")
        tokens_ins = SearchTokenizer.tokenize("памятью")

        # Все формы должны содержать нормализованную основу 'памят'
        assert any("памят" in t for t in tokens_nom)
        assert any("памят" in t for t in tokens_gen)
        assert any("памят" in t for t in tokens_ins)

    def test_compound_technical_terms_decomposition(self):
        """Сложное слово 'видеопамять' должно расщепляться на 'видео' и 'память'."""
        tokens = SearchTokenizer.tokenize("видеопамять")
        assert "видео" in tokens
        assert any("памят" in t for t in tokens)

    def test_code_identifiers_and_hex_extraction(self):
        """Проверка извлечения snake_case, camelCase и hex-кодов."""
        phrase = "Функция vram_arbiter в классе GPUOwner упала с 0xc0000005"
        tokens = SearchTokenizer.tokenize(phrase)

        assert "vram_arbiter" in tokens
        assert "vram" in tokens
        assert "arbiter" in tokens or "арбитр" in tokens or any("арбитр" in t for t in tokens)
        assert "gpuowner" in tokens or "gpu" in tokens
        assert "0xc0000005" in tokens


class TestMarkdownChunkerIntegrity:
    """Проверка нарезки документов, структуры секций и защиты от монолитных блоков."""

    def test_large_code_block_is_split_cleanly(self):
        """Блок кода длиннее max_chunk не должен оставаться монолитом."""
        max_chunk = 1000
        # Генерируем псевдо-дерево файлов на ~4000 символов
        code_lines = [f"├── module_{i}.py   # Описание модуля номер {i} системы" for i in range(70)]
        long_code_block = "```\n" + "\n".join(code_lines) + "\n```"
        document = f"## 2. Архитектура файлов\n\n{long_code_block}\n"

        chunks = MarkdownChunker.chunk_document(
            text=document,
            filename="architecture.md",
            max_chunk=max_chunk,
            min_chunk=300
        )

        assert len(chunks) > 1, "Крупный блок кода должен быть разбит на несколько чанков"
        for chunk in chunks:
            # Чанк не должен быть гигантом
            assert chunk["char_count"] <= max_chunk * 1.5, f"Чанк слишком велик: {chunk['char_count']}"
            txt = chunk["text"].strip()
            # Если чанк содержит код, маркеры кодового блока должны быть валидными
            if "├──" in txt:
                assert txt.startswith("```"), "Фрагмент кода обязан начинаться с обратных кавычек"
                assert txt.endswith("```"), "Фрагмент кода обязан закрываться обратными кавычками"

    def test_breadcrumbs_and_section_inheritance(self):
        """Чанки должны наследовать актуальный заголовок секции."""
        doc = (
            "# Главный заголовок\n\nВведение.\n\n"
            "## Секция 1. Сетевые протоколы\n\nОписание протоколов.\n\n"
            "## Секция 2. Арбитраж ресурсов\n\nПравила переключения GPU.\n"
        )
        chunks = MarkdownChunker.chunk_document(doc, filename="doc.md", max_chunk=40, min_chunk=20)
        sections = [c["section"] for c in chunks]

        assert any("Секция 1" in s for s in sections)
        assert any("Секция 2" in s for s in sections)


class TestRAGContextBudgetAndRetrieval:
    """Проверка бюджета контекста и поисковой доступности целевых компонентов."""

    def test_bm25_finds_vram_arbiter_by_russian_query(self):
        """BM25 с новым токенизатором должен связать 'арбитр памяти' с 'vram_arbiter.py # Арбитр видеопамяти'."""
        chunks = [
            {"chunk_id": "c1", "text": "├── vram_arbiter.py  # Арбитр видеопамяти и монопольного доступа к GPU"},
            {"chunk_id": "c2", "text": "Фоновая очередь задач task_queue_worker.py извлечение памяти deep_history"},
            {"chunk_id": "c3", "text": "Аппаратные лимиты системы: GPU VRAM 8192 MB Budget для LM Studio"},
        ]
        bm25 = BM25Index()
        chunk_texts = [c["text"] for c in chunks]
        bm25.index(chunk_texts)

        results = bm25.search("Как называется арбитр памяти?", top_k=3)
        assert len(results) > 0
        top_idx, top_score = results[0]
        assert chunks[top_idx]["chunk_id"] == "c1", "Чанк с vram_arbiter.py обязан занять первое место в BM25"

    def test_search_index_has_no_giant_chunks(self):
        """Проверка реального индекса knowledge_base/search_index.json на отсутствие чанков-гигантов."""
        idx_path = Path("knowledge_base/search_index.json")
        if not idx_path.exists():
            pytest.skip("Индекс knowledge_base/search_index.json не найден (пропустите, если база еще не собрана)")

        import json
        with open(idx_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        chunks = data if isinstance(data, list) else data.get("chunks", [])

        assert len(chunks) > 0, "Индекс не должен быть пустым"
        for c in chunks:
            char_len = len(c.get("text", ""))
            # Ни один чанк не должен превышать лимит 3000 символов (ранее было 14467)
            assert char_len < 3000, f"Обнаружен чанк-монолит #{c.get('chunk_id')}: {char_len} симв."

    def test_e2e_vram_arbiter_included_in_context_snippet(self):
        """Сквозной тест: сниппет для LLM по вопросу о видеопамяти обязан содержать vram_arbiter.py."""
        idx_path = Path("knowledge_base/search_index.json")
        if not idx_path.exists():
            pytest.skip("База знаний не инициализирована")

        query = "Как называется файл в neonroute который отвечает за видеопамять?"
        docs = KnowledgeManager.find_relevant_documents(query, threshold=0.05)

        assert len(docs) > 0, "Должен быть найден хотя бы один документ"
        top_doc = docs[0]
        snippet = top_doc.get("content_snippet", "").lower()

        assert "vram_arbiter" in snippet, "Сниппет для модели обязан содержать имя файла vram_arbiter.py"
        assert len(top_doc.get("matched_chunks", [])) <= 6, "Количество чанков не должно превышать лимит MAX_CHUNKS_PER_FILE"
        assert len(snippet) <= 9000, "Суммарный размер контекста обязан укладываться в MAX_SNIPPET_BUDGET"