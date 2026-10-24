"""
Unit and integration tests for Knowledge Base stabilization patch:
1. Table chunking (header preservation across chunks, row boundary integrity, row overlap).
2. Gatekeeper false-positive filter (conversational small-talk suppression, BM25 == 0 dense threshold >= 0.75).
3. Session persistence of HUD knowledge sources (both chat_<id>.json and outputs/sessions/<id>/session.json, fallback reload).
4. Memory hygiene (sanitization of <KnowledgeBase>, <Document>, citations from LanceDB extraction buffer).
"""

import os
import json
import tempfile
import pytest
from pathlib import Path
from typing import List, Dict, Any
import numpy as np

from services.hybrid_search import (
    HybridSearchEngine,
    BM25Index,
    is_conversational_query,
    is_table_row,
    is_table_separator,
    chunk_table
)
from services.knowledge_manager import KnowledgeManager
from services.session_manager import SessionManager
from ui.controllers.assistant_flow_controller import sanitize_messages_for_memory


# ─────────────────────────────────────────────────────────────────────────────
# 1. ТЕСТЫ ЧАНКИНГА ТАБЛИЦ (TABLE-PRESERVING CHUNKING)
# ─────────────────────────────────────────────────────────────────────────────

def test_is_table_helpers():
    assert is_table_row("| Col1 | Col2 | Col3 |") is True
    assert is_table_row("| A | B |") is True
    assert is_table_row("Not a table row") is False
    assert is_table_row("| Incomplete") is False

    assert is_table_separator("|---|---|") is True
    assert is_table_separator("|:---|:---:|---:|") is True
    assert is_table_separator("| Col1 | Col2 |") is False


def test_chunk_table_preserves_headers_and_row_integrity():
    # Создаем длинную таблицу с 35 строками деталей (~2500 символов)
    headers = "| Номер детали | Описание компонента | Статус |"
    sep = "|---|---|---|"
    rows = [
        f"| PART-{i:03d} | Надежный гидравлический клапан высокого давления тип {i} | В наличии |"
        for i in range(1, 35)
    ]
    full_table = [headers, sep] + rows

    chunks = chunk_table(full_table, max_chunk=700, overlap_rows=1)

    # Должно получиться более одного чанка
    assert len(chunks) > 1

    for idx, c in enumerate(chunks):
        lines = c.splitlines()
        # Каждый чанк обязан начинаться со строки заголовка и разделителя
        assert lines[0] == headers, f"Чанк {idx} потерял строку заголовков"
        assert lines[1] == sep, f"Чанк {idx} потерял строку разделителя"

        # Все строки обязаны быть валидными строками таблицы
        for row_line in lines:
            assert is_table_row(row_line), f"Строка '{row_line}' в чанке {idx} повреждена (разрезана)"

        # Длина чанка не должна грубо превышать max_chunk
        assert len(c) <= 950


def test_chunk_text_with_embedded_markdown_table():
    # Документ со вступительным текстом, таблицей и заключением
    intro = "Ниже представлена спецификация оборудования для системы охлаждения сервера.\n\n"
    headers = "| Код | Узел | Примечание |"
    sep = "|---|---|---|"
    rows = [f"| 0x{i:04X} | Вентилятор охлаждения модель N-{i} | Штатный режим работы |" for i in range(30)]
    table_str = "\n".join([headers, sep] + rows)
    outro = "\n\nВсе компоненты прошли обязательное тестирование на стенде."

    doc_text = intro + table_str + outro

    chunks = HybridSearchEngine.chunk_text(doc_text, filename="cooling_spec.csv", max_chunk=800)

    assert len(chunks) >= 2
    # Проверяем, что чанки таблицы содержат шапку
    table_chunks = [c for c in chunks if "| 0x" in c["text"]]
    assert len(table_chunks) >= 2
    for tc in table_chunks:
        assert headers in tc["text"]
        assert sep in tc["text"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. ТЕСТЫ GATEKEEPER И РАЗГОВОРНОГО ФИЛЬТРА (SMALL TALK SUPPRESSION)
# ─────────────────────────────────────────────────────────────────────────────

def test_conversational_queries_detection():
    # Позитивные проверки (small talk)
    assert is_conversational_query("Привет") is True
    assert is_conversational_query("привет, как дела?") is True
    assert is_conversational_query("Добрый день!") is True
    assert is_conversational_query("Спасибо большое!") is True
    assert is_conversational_query("Привет, Элария") is True
    assert is_conversational_query("hi elaria") is True
    assert is_conversational_query("how are you?") is True
    assert is_conversational_query("Ясно, спасибо") is True

    # Негативные проверки (содержательные вопросы)
    assert is_conversational_query("Что такое термостат 02T 311 239 B?") is False
    assert is_conversational_query("Как настроить систему охлаждения CPU?") is False
    assert is_conversational_query("Ошибка 0xc0000005 в приложении") is False
    assert is_conversational_query("Привет, расскажи подробно про двигатель 1.6") is False


def test_gatekeeper_suppresses_search_on_small_talk():
    engine = HybridSearchEngine()
    dummy_chunks = [
        {"chunk_id": "c1", "filename": "manual.txt", "text": "Инструкция по ремонту трансмиссии и коробки передач 02T 311."},
        {"chunk_id": "c2", "filename": "specs.txt", "text": "Охлаждение процессора и чипа кулером вентилятора."}
    ]
    engine.index_chunks(dummy_chunks)

    # Приветствие не должно вызывать поиск по мануалам
    results = engine.search("Привет, как дела?", threshold=0.01)
    assert results == []

    results_thanks = engine.search("Большое спасибо!", threshold=0.01)
    assert results_thanks == []


def test_gatekeeper_dense_threshold_when_bm25_zero():
    # Имитируем векторизатор с контролируемым скором
    class MockVectorizer:
        def __init__(self, target_dense: float):
            self.target_dense = target_dense

        def embed_text(self, text: str):
            # Вектор единичной длины
            vec = np.zeros(256, dtype=np.float32)
            vec[0] = 1.0
            return vec

        def embed_batch(self, texts: List[str]):
            # Возвращаем вектор, дающий dot product = target_dense
            mat = np.zeros((len(texts), 256), dtype=np.float32)
            mat[:, 0] = self.target_dense
            return mat

    # 1. Слабый Dense сходство (0.50 < 0.75) при BM25 == 0 -> Gatekeeper должен отсеять
    engine_low = HybridSearchEngine(dense_vectorizer=MockVectorizer(0.50))
    engine_low.index_chunks([{"chunk_id": "c1", "filename": "doc.txt", "text": "Инструкция по замене уплотнительных колец гидроцилиндра"}])
    # Запрос с абсолютно несовпадающими словами (BM25 = 0)
    hits_low = engine_low.search("космический телескоп хаббл спектрограф", threshold=0.05)
    assert len(hits_low) == 0

    # 2. Сильное Dense сходство (0.85 >= 0.75) при BM25 == 0 -> Gatekeeper пропускает
    engine_high = HybridSearchEngine(dense_vectorizer=MockVectorizer(0.85))
    engine_high.index_chunks([{"chunk_id": "c1", "filename": "doc.txt", "text": "Инструкция по замене уплотнительных колец гидроцилиндра"}])
    hits_high = engine_high.search("космический телескоп хаббл спектрограф", threshold=0.05)
    assert len(hits_high) == 1
    assert hits_high[0]["chunk_id"] == "c1"


def test_knowledge_manager_gatekeeper_rejects_greeting(tmp_path):
    # Тест интеграции KnowledgeManager: при запросе «Привет» реестр не возвращает мануалы
    k_dir = tmp_path / "kb"
    k_dir.mkdir()
    reg_file = k_dir / "registry.json"

    reg_data = {
        "version": 1,
        "total_documents": 1,
        "last_synced_at": "2026-10-24T00:00:00",
        "documents": {
            "transmission_manual.txt": {
                "filename": "transmission_manual.txt",
                "status": "parsed",
                "triggers": ["трансмиссия", "коробка", "передачи", "сцепление", "02t"],
                "content_preview": "Руководство по ремонту МКПП 02T 311 239 B"
            }
        }
    }
    reg_file.write_text(json.dumps(reg_data, ensure_ascii=False), encoding="utf-8")

    matched = KnowledgeManager.find_relevant_documents(
        "Привет!",
        custom_dir=k_dir,
        custom_registry=reg_file
    )
    assert matched == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. ТЕСТЫ ПЕРСИСТЕНТНОСТИ HUD-ИСТОЧНИКОВ В СЕССИИ (SESSION PERSISTENCE)
# ─────────────────────────────────────────────────────────────────────────────

def test_session_manager_saves_and_loads_knowledge_sources(tmp_path, monkeypatch):
    from config import HISTORY_DIR, OUTPUTS_DIR
    history_tmp = tmp_path / "history"
    outputs_tmp = tmp_path / "outputs"
    history_tmp.mkdir()
    outputs_tmp.mkdir()

    monkeypatch.setattr("services.session_manager.HISTORY_DIR", history_tmp)
    monkeypatch.setattr("services.session_manager.OUTPUTS_DIR", outputs_tmp)

    sess_id = "test_stabilization_sess_01"
    session_data = {
        "id": sess_id,
        "title": "Тестовый диалог со знаниями",
        "created_at": "2026-10-24T12:00:00",
        "messages": [
            {"role": "user", "text": "Как заменить клапан 02T?", "is_user": True},
            {
                "role": "assistant",
                "text": "Для замены клапана 02T следуйте регламенту...",
                "is_user": False,
                "knowledge_sources": [
                    {"filename": "valve_spec.csv", "chunk_id": "#c2", "score": 0.88, "snippet": "Клапан 02T..."},
                    {"filename": "manual.pdf", "chunk_id": "#c0", "score": 0.74, "snippet": "Общий регламент..."}
                ]
            }
        ]
    }

    # 1. Сохранение сессии
    saved_path = SessionManager.save_session(session_data)
    assert saved_path is not None
    assert saved_path.exists()

    # Проверяем, что создался файл в HISTORY_DIR (chat_<id>.json)
    chat_file = history_tmp / f"chat_{sess_id}.json"
    assert chat_file.exists()

    # Проверяем, что создался файл в outputs/sessions/<id>/session.json
    session_json_file = outputs_tmp / "sessions" / sess_id / "session.json"
    assert session_json_file.exists()

    # Проверяем целостность сохраненных knowledge_sources в session.json
    with open(session_json_file, "r", encoding="utf-8") as f:
        loaded_json = json.load(f)
    ast_msg = loaded_json["messages"][1]
    assert "knowledge_sources" in ast_msg
    assert len(ast_msg["knowledge_sources"]) == 2
    assert ast_msg["knowledge_sources"][0]["filename"] == "valve_spec.csv"

    # 2. Загрузка через load_session
    reloaded = SessionManager.load_session(sess_id)
    assert reloaded is not None
    r_sources = reloaded["messages"][1].get("knowledge_sources")
    assert r_sources is not None
    assert len(r_sources) == 2

    # 3. Тест фоллбэка: удаляем chat_<id>.json, load_session должен подхватить session.json
    chat_file.unlink()
    fallback_loaded = SessionManager.load_session(sess_id)
    assert fallback_loaded is not None
    assert fallback_loaded["messages"][1]["knowledge_sources"][0]["chunk_id"] == "#c2"


# ─────────────────────────────────────────────────────────────────────────────
# 4. ТЕСТЫ ОЧИСТКИ ПАМЯТИ (PERSONAL MEMORY HYGIENE)
# ─────────────────────────────────────────────────────────────────────────────

def test_sanitize_messages_for_memory():
    dirty_messages = [
        {
            "role": "user",
            "text": "Привет! Меня зовут Алекс, я работаю системным инженером. Помоги с сервером."
        },
        {
            "role": "assistant",
            "text": (
                "<KnowledgeBase>\n"
                "<Document filename=\"server_specs.csv\">\n"
                "| Узел | Напряжение | Допуск |\n"
                "|---|---|---|\n"
                "| CPU_VCORE | 1.2V | +-5% |\n"
                "</Document>\n"
                "</KnowledgeBase>\n\n"
                "Привет, Алекс! Согласно [Источник: server_specs.csv] [Chunk #c1], "
                "допустимое напряжение ядра составляет 1.2V."
            )
        },
        {
            "role": "user",
            "text": (
                "<AttachedDocument filename=\"notes.txt\">\n"
                "Служебный дамп конфигурации\n"
                "</AttachedDocument>\n"
                "Я живу в Новосибирске и люблю пить кофе по утрам."
            )
        }
    ]

    cleaned = sanitize_messages_for_memory(dirty_messages)

    assert len(cleaned) == 3

    # Проверяем пользователя 1
    assert "Меня зовут Алекс" in cleaned[0]["text"]

    # Проверяем ассистента: теги и цитаты вырезаны, связный текст остался
    ast_clean = cleaned[1]["text"]
    assert "<KnowledgeBase>" not in ast_clean
    assert "</KnowledgeBase>" not in ast_clean
    assert "<Document" not in ast_clean
    assert "CPU_VCORE" not in ast_clean
    assert "[Источник:" not in ast_clean
    assert "[Chunk" not in ast_clean
    assert "Привет, Алекс!" in ast_clean

    # Проверяем пользователя 2: AttachedDocument вырезан, личный факт сохранен
    usr_clean = cleaned[2]["text"]
    assert "<AttachedDocument" not in usr_clean
    assert "Служебный дамп" not in usr_clean
    assert "Я живу в Новосибирске" in usr_clean
