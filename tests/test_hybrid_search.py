"""
Unit and integration tests for Elaria's Local Hybrid Search (services/hybrid_search.py).
Strictly CPU-based (0 MB VRAM, <50 ms latency).
Covers:
1. Exact BM25 search for error codes (0xC0000005, ERR_CONNECTION_RESET) and part numbers (02T 311 239 B).
2. Dense semantic search for paraphrased queries.
3. Chunking logic (600-900 chars with ~100 char overlap).
4. RRF (Reciprocal Rank Fusion) and noise threshold filtering.
5. Fast CPU latency (<50 ms) and zero VRAM allocation.
6. Integration with KnowledgeManager and AssistantPromptBuilder chunk formatting.
"""

import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from services.hybrid_search import BM25Index, BuiltinDenseVectorizer, HybridSearchEngine
from services.knowledge_manager import KnowledgeManager
from services.assistant_prompt_builder import AssistantPromptBuilder


def test_bm25_exact_error_codes():
    """Проверка точного поиска BM25 по шестнадцатеричным и строковым кодам ошибок."""
    bm25 = BM25Index()
    corpus = [
        "Системный сбой 0xC0000005 произошел при обращении к защищенной области памяти.",
        "Сетевая ошибка ERR_CONNECTION_RESET: соединение было неожиданно разорвано сервером.",
        "Код ответа HTTP 404_NOT_FOUND: запрошенный ресурс не найден.",
        "Обычный справочный текст о настройке рабочего стола и оформлении интерфейса."
    ]
    bm25.index(corpus)

    # 1. Поиск по 0xC0000005
    res_hex = bm25.search("0xC0000005", top_k=2)
    assert len(res_hex) > 0
    assert res_hex[0][0] == 0  # Документ 0 на первом месте

    # 2. Поиск по ERR_CONNECTION_RESET
    res_err = bm25.search("ERR_CONNECTION_RESET", top_k=2)
    assert len(res_err) > 0
    assert res_err[0][0] == 1  # Документ 1 на первом месте

    # 3. Поиск по 404_NOT_FOUND
    res_404 = bm25.search("404_NOT_FOUND", top_k=2)
    assert len(res_404) > 0
    assert res_404[0][0] == 2  # Документ 2 на первом месте


def test_bm25_exact_part_numbers():
    """Проверка точного поиска BM25 по составным артикулам и номерам деталей с пробелами и дефисами."""
    bm25 = BM25Index()
    corpus = [
        "Спецификация коробки передач VAG: деталь 02T 311 239 B является вторичным валом передачи.",
        "Руководство по замене ремня ГРМ для артикула A123-456-789 на двигателе 1.6.",
        "Инструкция по замене салонного фильтра 1K0 819 644 B.",
        "Рецепты кофейных напитков и выпечки для кафе."
    ]
    bm25.index(corpus)

    # Поиск по составному артикулу с пробелами: '02T 311 239 B'
    res_part = bm25.search("02T 311 239 B", top_k=2)
    assert len(res_part) > 0
    assert res_part[0][0] == 0

    # Поиск по дефисному номеру: 'A123-456-789'
    res_hyphen = bm25.search("A123-456-789", top_k=2)
    assert len(res_hyphen) > 0
    assert res_hyphen[0][0] == 1

    # Поиск по фильтру '1K0 819 644 B'
    res_filter = bm25.search("1K0 819 644 B", top_k=2)
    assert len(res_filter) > 0
    assert res_filter[0][0] == 2


def test_dense_semantic_paraphrased_search():
    """Проверка Dense семантического поиска по перефразированным запросам без ключевых совпадений."""
    engine = HybridSearchEngine()
    corpus = [
        {"chunk_id": "c0", "filename": "cooler.txt", "chunk_index": 0, "text": "Эффективное охлаждение процессора, настройка кулера, термопаста и снижение нагрева кристалла."},
        {"chunk_id": "c1", "filename": "brakes.txt", "chunk_index": 0, "text": "Замена тормозных колодок, проточка дисков и обслуживание переднего суппорта автомобиля."},
        {"chunk_id": "c2", "filename": "music.txt", "chunk_index": 0, "text": "Теория музыки, нотная грамота, гармонические интервалы и аккорды фортепиано."}
    ]
    engine.index_chunks(corpus)

    # Запрос перефразирован: 'как охладить чип и снизить температуру CPU'
    hits = engine.search("как охладить чип и снизить температуру CPU", top_k=2)
    assert len(hits) > 0
    assert hits[0]["chunk_id"] == "c0"
    assert hits[0]["dense_score"] > 0.2

    # Запрос по тормозам: 'инструкция по ремонту суппортов и колодок'
    brake_hits = engine.search("инструкция по ремонту суппортов и колодок", top_k=2)
    assert len(brake_hits) > 0
    assert brake_hits[0]["chunk_id"] == "c1"


def test_chunking_boundaries_and_overlap():
    """Проверка нарезки текста на чанки 600–900 символов с перекрытием ~100 символов."""
    para1 = "Первый блок документа содержит подробные инструкции по инициализации системных библиотек. " * 10
    para2 = "Второй блок описывает распределение буферов памяти и работу с сокетами в фоновом режиме. " * 10
    para3 = "Третий блок посвящен протоколированию сетевых пакетов и диагностике разрывов соединения. " * 10
    full_text = f"{para1}\n\n{para2}\n\n{para3}"

    chunks = HybridSearchEngine.chunk_text(full_text, filename="spec.txt", min_chunk=600, max_chunk=900, overlap=100)
    assert len(chunks) >= 3

    for i, ch in enumerate(chunks):
        assert ch["filename"] == "spec.txt"
        assert ch["chunk_id"] == f"spec.txt#c{i}"
        # Основные чанки укладываются в диапазон 600-950 символов
        if i < len(chunks) - 1:
            assert 600 <= ch["char_count"] <= 950
        assert len(ch["text"]) > 0

    # Проверка наличия перекрытия между соседними чанками (слова из конца первого присутствуют в начале второго)
    first_chunk_end = chunks[0]["text"][-80:]
    second_chunk_start = chunks[1]["text"][:150]
    overlap_found = any(word in second_chunk_start for word in first_chunk_end.split() if len(word) >= 5)
    assert overlap_found, "Перекрытие между соседними чанками должно сохранять фрагменты текста"


def test_rrf_reranking_and_noise_filtering():
    """Проверка переранжирования через RRF и отсечки шума по порогу threshold."""
    engine = HybridSearchEngine(alpha=0.5, rrf_k=60)
    corpus = [
        {"chunk_id": "art_1", "filename": "car.txt", "chunk_index": 0, "text": "Деталь 02T 311 239 B установлена в механической коробке передач."},
        {"chunk_id": "art_2", "filename": "car.txt", "chunk_index": 1, "text": "Обслуживание трансмиссии, замена масла и диагностика КПП автомобиля."},
        {"chunk_id": "noise", "filename": "recipe.txt", "chunk_index": 0, "text": "Приготовление яблочного штруделя с корицей и ванильным мороженым."}
    ]
    engine.index_chunks(corpus)

    # 1. Поиск с шумом: нерелевантный чанк 'noise' должен быть отфильтрован
    results = engine.search("артикул 02T 311 239 B коробка передач", top_k=3, threshold=0.15)
    result_ids = [r["chunk_id"] for r in results]
    assert "noise" not in result_ids
    assert "art_1" in result_ids
    assert results[0]["chunk_id"] == "art_1"

    # 2. Высокий порог threshold должен отсеять всё, если запрос полностью о постороннем
    unrelated = engine.search("квантовая запутанность бозонов Хиггса", top_k=3, threshold=0.6)
    assert len(unrelated) == 0


def test_performance_latency_and_zero_vram():
    """Проверка скорости поиска (<50 мс на CPU) и отсутствия выделения VRAM."""
    engine = HybridSearchEngine()
    # Создаем 50 чанков
    synthetic_chunks = []
    for i in range(50):
        synthetic_chunks.append({
            "chunk_id": f"doc_{i//5}.txt#c{i%5}",
            "filename": f"doc_{i//5}.txt",
            "chunk_index": i % 5,
            "text": f"Документ номер {i}. Содержит техническую информацию о модуле {i*7} и коде ошибки 0x{i:04X}."
        })
    engine.index_chunks(synthetic_chunks)

    # Замеряем время поиска на CPU
    t0 = time.perf_counter()
    res = engine.search("ошибка модуля 0x000E", top_k=3)
    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000.0

    assert latency_ms < 50.0, f"Поиск занял {latency_ms:.2f} мс, что превышает лимит 50 мс"
    assert len(res) > 0

    # Проверка 0 МБ VRAM: убеждаемся, что PyTorch CUDA не используется
    try:
        import torch
        if torch.cuda.is_available():
            assert torch.cuda.memory_allocated() == 0, "VRAM не должна использоваться гибридным поиском!"
    except ImportError:
        pass  # Если PyTorch не установлен, VRAM гарантированно 0 МБ


def test_prompt_builder_chunk_xml_format():
    """Проверка генерации контекста <Document chunk_id="..." name="..."> для чанков."""
    chunk_docs = [
        {
            "chunk_id": "guide.pdf#c2",
            "filename": "guide.pdf",
            "text": "Для устранения ошибки 0xC0000005 проверьте указатель на структуру памяти."
        },
        {
            "chunk_id": "manual.docx#c0",
            "filename": "manual.docx",
            "content": "Деталь 02T 311 239 B требует момента затяжки 85 Нм."
        }
    ]

    xml = AssistantPromptBuilder.build_knowledge_context(chunk_docs)
    assert "<KnowledgeBase>" in xml
    assert "</KnowledgeBase>" in xml
    assert '<Document chunk_id="guide.pdf#c2" name="guide.pdf">' in xml
    assert "0xC0000005" in xml
    assert '<Document chunk_id="manual.docx#c0" name="manual.docx">' in xml
    assert "02T 311 239 B" in xml


def test_knowledge_manager_hybrid_integration(tmp_path: Path, monkeypatch):
    """Интеграционный тест: создание базы знаний, нарезка на чанки, кэш и гибридный поиск."""
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"
    idx_file = kb_dir / "search_index.json"

    # Создаем файл с подробным описанием детали
    doc_path = kb_dir / "transmission.txt"
    doc_path.write_text(
        "Каталог запчастей. Шестерня пятой передачи имеет номер 02T 311 239 B. "
        "При замене используйте оригинальное трансмиссионное масло. " * 8,
        encoding="utf-8"
    )

    monkeypatch.setattr(KnowledgeManager, "get_knowledge_dir", lambda custom_dir=None: kb_dir)
    monkeypatch.setattr(KnowledgeManager, "get_registry_path", lambda custom_path=None, custom_dir=None: reg_file)
    monkeypatch.setattr(KnowledgeManager, "get_search_index_path", lambda custom_path=None, custom_dir=None: idx_file)

    # 1. Синхронизация: парсинг, извлечение триггеров и нарезка на чанки
    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)

    assert reg_file.exists(), "registry.json должен быть создан"
    assert idx_file.exists(), "search_index.json должен быть создан"

    # 2. Прямой поиск по чанкам через search_chunks
    chunks_found = KnowledgeManager.search_chunks("02T 311 239 B", top_k=2, custom_dir=kb_dir)
    assert len(chunks_found) > 0
    assert chunks_found[0]["filename"] == "transmission.txt"
    assert "02T 311 239 B" in chunks_found[0]["text"]

    # 3. Поиск через find_relevant_documents с прикреплением чанка
    relevant_docs = KnowledgeManager.find_relevant_documents("02T 311 239 B", threshold=0.2, custom_dir=kb_dir)
    assert len(relevant_docs) > 0
    assert relevant_docs[0]["filename"] == "transmission.txt"
    assert "02T 311 239 B" in relevant_docs[0]["content_snippet"]

    # 4. Получение сниппета с query
    snippet = KnowledgeManager.get_document_knowledge_snippet("transmission.txt", query="02T 311 239 B", custom_dir=kb_dir)
    assert snippet is not None
    assert "02T 311 239 B" in snippet
