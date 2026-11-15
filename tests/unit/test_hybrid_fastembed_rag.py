import numpy as np
from pathlib import Path
from unittest.mock import patch
from services.knowledge_manager import KnowledgeManager
from services.hybrid_search import HybridSearchEngine


class _UnnormalizedNeuralEmbedder:
    """Эмулятор FastEmbed (384d) с ненормализованными сырыми векторами (L2 норма ~ 4.8),
    проверяющий L2-нормализацию, сериализацию to_dict/from_dict и порог Gatekeeper 0.55 в pytest-airgap."""

    def _encode_one(self, text: str) -> np.ndarray:
        t = text.lower()
        vec = np.full(384, 0.05, dtype=np.float32)
        # Кластер 1: Аренда / оплата / штраф / пеня / квартира / помещение
        if any(k in t for k in ("арендатор", "просрочк", "пеня", "помещен", "штраф", "задерж", "оплат", "квартир")):
            vec[0:64] = 0.55
        # Кластер 2: Кросс-языковой (RU/EN) токен авторизации / срок действия
        if any(k in t for k in ("authentication", "token", "expires", "renew", "токен", "авторизац", "срок")):
            vec[64:128] = 0.58
        # Кластер 3: Кулинария / пирог
        if any(k in t for k in ("рецепт", "пирог", "духовк", "кориц")):
            vec[128:192] = 0.60
        # Кластер 4: Посторонний шум (анекдот)
        if any(k in t for k in ("анекдот", "смешн")):
            vec[192:256] = 0.60
        return vec  # Сырая L2-норма ~ 4.5 (как у реального FastEmbed до нормализации)

    def embed(self, texts):
        for txt in texts:
            yield self._encode_one(txt)


def test_fastembed_l2_normalization_and_dict_roundtrip():
    neural_mock = _UnnormalizedNeuralEmbedder()
    engine = HybridSearchEngine(dense_vectorizer=neural_mock)
    chunks = [
        {
            "chunk_id": "c1",
            "filename": "rent_contract.md",
            "text": "Арендатор обязан ежемесячно перечислять плату на расчетный счет владельца помещения не позднее 5-го числа. При просрочке начисляется пеня 0.5% в день."
        },
        {
            "chunk_id": "c2",
            "filename": "api_docs.md",
            "text": "Authentication token expires every 24 hours. To refresh credentials, send a POST request to /v1/auth/renew."
        },
        {
            "chunk_id": "c3",
            "filename": "recipes.md",
            "text": "Рецепт приготовления яблочного пирога в духовке с корицей и тростниковым сахаром."
        }
    ]
    engine.index_chunks(chunks)

    # 1. Проверяем, что матрица 384-мерная и строго L2-нормализована (длина каждого вектора == 1.0)
    assert engine.dense_matrix.shape == (3, 384)
    norms = np.linalg.norm(engine.dense_matrix, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)

    # 2. Проверяем сериализацию/десериализацию (to_dict -> from_dict) без AttributeError
    saved = engine.to_dict()
    assert saved.get("dense_dim") == 384
    assert len(saved.get("dense_vectors", [])) == 3

    restored = HybridSearchEngine(dense_vectorizer=neural_mock)
    restored.from_dict(saved)
    assert restored.dense_matrix.shape == (3, 384)
    assert np.allclose(np.linalg.norm(restored.dense_matrix, axis=1), 1.0, atol=1e-4)


def test_semantic_and_crosslingual_rag_search(tmp_path: Path):
    neural_mock = _UnnormalizedNeuralEmbedder()
    with patch.object(KnowledgeManager, "get_embedding_model", return_value=neural_mock):
        KnowledgeManager._search_engine_cache.clear()
        (tmp_path / "rent_contract.md").write_text(
            "# Договор найма\nАрендатор обязан ежемесячно перечислять плату на расчетный счет владельца помещения не позднее 5-го числа. При просрочке начисляется пеня 0.5% в день.",
            encoding="utf-8"
        )
        (tmp_path / "api_docs.md").write_text(
            "# API Security\nAuthentication token expires every 24 hours. To refresh credentials, send a POST request to /v1/auth/renew.",
            encoding="utf-8"
        )
        (tmp_path / "recipes.md").write_text(
            "# Выпечка\nРецепт приготовления яблочного пирога в духовке с корицей и тростниковым сахаром.",
            encoding="utf-8"
        )

        KnowledgeManager.scan_and_sync(custom_dir=tmp_path)

        # 1. Поиск по русским синонимам без общих ключевых слов
        rent_hits = KnowledgeManager.find_relevant_documents(
            "Какой штраф, если я задержу оплату за квартиру?",
            custom_dir=tmp_path
        )
        assert [h["filename"] for h in rent_hits] == ["rent_contract.md"]

        # 2. Кросс-языковой поиск (русский вопрос -> английский документ)
        api_hits = KnowledgeManager.find_relevant_documents(
            "Как обновить токен авторизации, когда закончился срок его действия?",
            custom_dir=tmp_path
        )
        assert [h["filename"] for h in api_hits] == ["api_docs.md"]

        # 3. Защита Gatekeeper от нерелевантного шума
        noise_hits = KnowledgeManager.find_relevant_documents(
            "Расскажи смешной анекдот про программиста",
            custom_dir=tmp_path
        )
        assert noise_hits == []


def test_bm25_tf_stopwords_and_next_chunk_continuity(tmp_path: Path):
    """Проверяет, что:
    1. Служебный союз 'что' в вопросе 'Что такое NeonRoute?' отфильтровывается и не перебивает чанк с 3 вхождениями 'NeonRoute'.
    2. За главным чанком #c0 автоматически подтягивается смежный чанк продолжения #c1 в правильном порядке чтения.
    """
    intro_part = (
        "# Техническая спецификация архитектуры NeonRoute\n\n"
        "Версия системы: NeonRoute v0.9.8-beta. Система **NeonRoute** — это мультимодальный десктопный ИИ-клиент. "
        + ("Архитектура ориентирована на локальный инференс. " * 12)
    )
    continuation_part = (
        "Система бесшовно объединяет:\n"
        "1. Большие языковые модели (LLM).\n"
        "2. Диффузионный рендеринг Stable Diffusion Forge.\n"
        "3. Гибридный векторный поиск RAG на CPU.\n"
        + ("Подробное описание модулей и контуров безопасности. " * 12)
    )
    distractor_part = (
        "### Вспомогательный сетевой модуль\n"
        "Проверка порта в NeonRoute сигнализирует, что сокет занят, что предотвращает конфликт портов."
    )
    doc_text = f"{intro_part}\n\n{continuation_part}\n\n{distractor_part}"
    (tmp_path / "ARCHITECTURE.md").write_text(doc_text, encoding="utf-8")

    KnowledgeManager._search_engine_cache.clear()
    KnowledgeManager.scan_and_sync(custom_dir=tmp_path)

    hits = KnowledgeManager.find_relevant_documents("Что такое neonroute?", custom_dir=tmp_path)
    assert len(hits) == 1
    matched_chunks = hits[0].get("matched_chunks", [])
    chunk_ids = [c["chunk_id"] for c in matched_chunks]

    # Первыми двумя чанками в порядке чтения должны идти #c0 (введение) и #c1 (список компонентов)
    assert chunk_ids[:2] == ["ARCHITECTURE.md#c0", "ARCHITECTURE.md#c1"]
    assert "Система бесшовно объединяет:" in hits[0]["content_snippet"]
