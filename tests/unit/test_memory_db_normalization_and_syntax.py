"""
Unit tests for MemoryDB L2 normalization, cosine metric, deduplication, and safe SQL/LanceDB syntax.
Strictly isolated in temporary database without touching production data.
"""

import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import pytest

from services.memory_db import MemoryDB, _normalize_l2
import lancedb

class MockVectorEmbedder:
    """Детерминированный легковесный векторный эмбеддер для изолированных unit-тестов."""
    def __init__(self, dim=384):
        self.dim = dim

    def embed(self, texts):
        for t in texts:
            low = str(t).lower()
            vec = np.zeros(self.dim, dtype=np.float32)
            if "init" in low:
                vec[0] = 1.0
            elif "кофе" in low:
                vec[10] = 1.0
                if "какой" in low or "предпочитаю" in low:
                    vec[11] = 0.2
            elif "python" in low or "rust" in low:
                vec[20] = 1.0
                if "языках" in low or "код" in low:
                    vec[21] = 0.05
            elif "спутник" in low or "юпитер" in low:
                vec[30] = 1.0
            elif "удален" in low:
                vec[40] = 1.0
            elif "факт 1" in low:
                vec[50] = 1.0
            elif "факт 2" in low:
                vec[60] = 1.0
            elif "чай" in low:
                vec[70] = 1.0
            else:
                idx = (hash(low) % 100) + 100
                vec[idx] = 1.0
            yield _normalize_l2(vec.tolist())

@pytest.fixture
def isolated_memory_db():
    temp_dir = Path(tempfile.mkdtemp(prefix="test_mem_db_"))
    db_file = temp_dir / "memory.db"
    lance_dir = temp_dir / "vector_memory"
    lance_dir.mkdir(parents=True, exist_ok=True)

    embed_model = MockVectorEmbedder()
    db_lance = lancedb.connect(str(lance_dir))
    dummy_vec = list(embed_model.embed(["init"]))[0]
    tbl_facts = db_lance.create_table("facts", data=[{"id": "init_id", "text": "init", "vector": dummy_vec}])

    with patch("services.memory_db._get_embedding_engine", return_value=(embed_model, tbl_facts, None)), \
         patch("services.memory_db._EMBED_MODEL", embed_model), \
         patch("services.memory_db._LANCE_TABLE", tbl_facts):
        mem_db = MemoryDB(db_path=db_file)
        yield mem_db, tbl_facts, embed_model

    shutil.rmtree(temp_dir, ignore_errors=True)

def test_l2_normalization_helper():
    raw_vec = [3.0, 4.0, 0.0]
    norm_vec = _normalize_l2(raw_vec)
    length = sum(x**2 for x in norm_vec)**0.5
    assert abs(length - 1.0) < 1e-6
    assert abs(norm_vec[0] - 0.6) < 1e-6
    assert abs(norm_vec[1] - 0.8) < 1e-6

def test_add_fact_normalizes_and_stores_vector(isolated_memory_db):
    mem_db, _, _ = isolated_memory_db
    fid = mem_db.add_fact("Я люблю пить чай с лимоном")
    assert fid > 0

    rows = mem_db.table.to_arrow().to_pylist()
    stored = next(r for r in rows if r["id"] == str(fid))
    assert "чай" in stored["text"].lower()
    norm = sum(x**2 for x in stored["vector"])**0.5
    assert abs(norm - 1.0) < 1e-4

def test_semantic_deduplication_threshold(isolated_memory_db):
    mem_db, tbl_facts, _ = isolated_memory_db
    fid1 = mem_db.add_fact("Пользователь пишет на Python и Rust")
    assert fid1 > 0

    # Семантически эквивалентная фраза должна опознаться (dist < 0.15) и не создавать дубликат
    fid2 = mem_db.add_fact("Я пишу код на языках Python и Rust")
    assert fid2 == fid1

    all_facts = mem_db.get_all_facts()
    assert len(all_facts) == 1

def test_search_relevant_facts_cosine_threshold(isolated_memory_db):
    mem_db, _, _ = isolated_memory_db
    mem_db.add_fact("Пользователь предпочитает черный кофе без сахара")
    mem_db.add_fact("Пользователь пишет на Python и Rust")

    # Релевантный запрос
    hits = mem_db.search_relevant_facts("Какой кофе я предпочитаю?", top_k=5)
    assert len(hits) >= 1
    assert "кофе" in hits[0].lower()

    # Несвязанный запрос (шум) должен возвращать пустой список
    noise_hits = mem_db.search_relevant_facts("Сколько спутников у Юпитера?", top_k=5)
    assert len(noise_hits) == 0

def test_delete_fact_safe_syntax_and_quotes(isolated_memory_db):
    mem_db, tbl_facts, _ = isolated_memory_db
    fid = mem_db.add_fact("Временный факт для проверки удаления")
    assert fid > 0

    deleted = mem_db.delete_fact_by_id(fid)
    assert deleted is True

    # Проверяем, что в SQLite и LanceDB факта больше нет
    all_facts = mem_db.get_all_facts()
    assert not any(f["id"] == fid for f in all_facts)

    lance_rows = [r for r in tbl_facts.to_arrow().to_pylist() if r["id"] == str(fid)]
    assert len(lance_rows) == 0

def test_sync_and_reindex_facts_cleans_orphans(isolated_memory_db):
    mem_db, tbl_facts, _ = isolated_memory_db
    f1 = mem_db.add_fact("Факт 1")
    f2 = mem_db.add_fact("Факт 2")

    # Добавляем мусорную/осиротевшую запись напрямую в LanceDB
    tbl_facts.add([{"id": "999", "text": "Сирота", "vector": [0.1]*384}])
    assert len(tbl_facts.to_arrow().to_pylist()) == 4  # init_id + 2 facts + 1 orphan

    reindexed = mem_db.sync_and_reindex_facts()
    assert reindexed == 2

    # Сирота должна исчезнуть
    remaining_ids = {r["id"] for r in tbl_facts.to_arrow().to_pylist() if r["id"] != "init_id"}
    assert remaining_ids == {str(f1), str(f2)}
