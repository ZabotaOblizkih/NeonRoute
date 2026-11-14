r"""
Local Long-Term User Memory Service for NeonRoute.
Backed by SQLite (C:\\NeonRoute\data\memory.db) and LanceDB vector store.
Runs strictly on CPU via FastEmbed ONNX to preserve 8GB VRAM for LLM & Forge.
Features semantic and exact deduplication and pronoun normalization.
"""

import os
import re
import sqlite3
import logging
import time
import threading
from datetime import datetime, timedelta
import atexit
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

from config import MEMORY_DB_PATH, DATA_DIR

logger = logging.getLogger("NeonRoute.MemoryDB")

_EMBED_INIT_LOCK = threading.Lock()
_LANCE_LOCK = threading.RLock()
_EMBED_MODEL = None
_LANCE_TABLE = None
_RP_LANCE_TABLE = None

def _normalize_l2(vector: List[float]) -> List[float]:
    """Выполняет L2-нормализацию вектора (приведение к единичной евклидовой норме)."""
    try:
        arr = np.array(vector, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        return arr.tolist()
    except Exception:
        return vector

def _get_embedding_engine():
    global _EMBED_MODEL, _LANCE_TABLE, _RP_LANCE_TABLE
    with _EMBED_INIT_LOCK:
        # 1. Инициализация FastEmbed (если еще не создана и не отключена)
        if _EMBED_MODEL is None:
            try:
                from fastembed import TextEmbedding
                _EMBED_MODEL = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
            except Exception as e:
                logger.warning(f"FastEmbed initialization error: {e}")
                _EMBED_MODEL = False

        # 2. Инициализация LanceDB таблиц (независимо от того, откуда пришел _EMBED_MODEL)
        if (_LANCE_TABLE is None or _RP_LANCE_TABLE is None) and _LANCE_TABLE is not False and _RP_LANCE_TABLE is not False:
            try:
                import lancedb
                lance_path = DATA_DIR / "vector_memory"
                lance_path.mkdir(parents=True, exist_ok=True)
                db = lancedb.connect(str(lance_path))
                
                with _LANCE_LOCK:
                    raw_tables = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
                    tables_iter = getattr(raw_tables, "tables", raw_tables)
                    table_names = set(tables_iter) if isinstance(tables_iter, (list, tuple, set)) else set()

                    # 1. Глобальная память ассистента
                    if _LANCE_TABLE is None:
                        try:
                            if "facts" in table_names:
                                _LANCE_TABLE = db.open_table("facts")
                            else:
                                if _EMBED_MODEL and hasattr(_EMBED_MODEL, "embed"):
                                    dummy_vec = _normalize_l2(list(_EMBED_MODEL.embed(["init"]))[0].tolist())
                                else:
                                    dummy_vec = [0.0] * 384
                                _LANCE_TABLE = db.create_table("facts", data=[{"id": "init_id", "text": "init", "vector": dummy_vec}])
                        except Exception:
                            _LANCE_TABLE = db.open_table("facts")

                    # 2. Изолированная сессионная память для ролевых чатов
                    if _RP_LANCE_TABLE is None:
                        try:
                            if "rp_memories" in table_names:
                                _RP_LANCE_TABLE = db.open_table("rp_memories")
                            else:
                                if _EMBED_MODEL and hasattr(_EMBED_MODEL, "embed"):
                                    dummy_vec = _normalize_l2(list(_EMBED_MODEL.embed(["init"]))[0].tolist())
                                else:
                                    dummy_vec = [0.0] * 384
                                _RP_LANCE_TABLE = db.create_table(
                                    "rp_memories",
                                    data=[{
                                        "id": "init_id",
                                        "session_id": "init_sess",
                                        "text": "init",
                                        "vector": dummy_vec,
                                        "timestamp": "0"
                                    }]
                                )
                        except Exception:
                            _RP_LANCE_TABLE = db.open_table("rp_memories")

                logger.info("FastEmbed CPU ONNX and LanceDB (facts + rp_memories) ready.")
            except Exception as e:
                logger.warning(f"LanceDB initialization fallback: {e}")
                _LANCE_TABLE = False
                _RP_LANCE_TABLE = False
    return _EMBED_MODEL, _LANCE_TABLE, _RP_LANCE_TABLE

_LAST_LANCE_CLEANUP = 0.0
_WRITE_OP_COUNT = 0
_CLEANUP_EVERY_N_WRITES = 20

def _maybe_cleanup_lance_tables(force: bool = False):
    """
    Периодическая очистка старых версий и фрагментов LanceDB.
    Срабатывает раз в 300 секунд или каждые N операций записи, не блокируя запуск.
    """
    global _LAST_LANCE_CLEANUP, _WRITE_OP_COUNT
    _WRITE_OP_COUNT += 1
    now = time.time()
    if not force and (_WRITE_OP_COUNT < _CLEANUP_EVERY_N_WRITES) and (now - _LAST_LANCE_CLEANUP < 300):
        return
    _LAST_LANCE_CLEANUP = now
    _WRITE_OP_COUNT = 0
    try:
        if "memory_db" in globals() and globals()["memory_db"] is not None:
            globals()["memory_db"].optimize_tables()
        else:
            _, facts_t, rp_t = _get_embedding_engine()
            for tbl_name, tbl in [("facts", facts_t), ("rp_memories", rp_t)]:
                if not tbl:
                    continue
                with _LANCE_LOCK:
                    try:
                        optimized = False
                        if hasattr(tbl, "optimize"):
                            try:
                                tbl.optimize(cleanup_older_than=timedelta(0), delete_unverified=True)
                                optimized = True
                            except Exception as e_opt:
                                logger.debug(f"[MemoryDB] optimize fallback for '{tbl_name}': {e_opt}")
                        if not optimized:
                            if hasattr(tbl, "compact_files"):
                                try:
                                    tbl.compact_files()
                                except Exception as e_compact:
                                    logger.debug(f"[MemoryDB] compact_files fallback for '{tbl_name}': {e_compact}")
                            if hasattr(tbl, "cleanup_old_versions"):
                                try:
                                    tbl.cleanup_old_versions(older_than=timedelta(0), delete_unverified=True)
                                except Exception:
                                    try:
                                        tbl.cleanup_old_versions()
                                    except Exception as e_clean:
                                        logger.debug(f"[MemoryDB] cleanup_old_versions fallback for '{tbl_name}': {e_clean}")
                        logger.info(f"Таблица LanceDB '{tbl_name}' успешно оптимизирована.")
                    except Exception as e:
                        logger.warning(f"Ошибка при компактизации таблицы LanceDB '{tbl_name}': {e}")
    except Exception as e:
        logger.debug(f"[MemoryDB] LanceDB cleanup error: {e}")

SAVE_TRIGGERS = [
    "занеси в память", "сохрани факт", "save fact", "запомни", "запиши", "remember"
]
VIEW_TRIGGERS = [
    "что ты обо мне знаешь", "покажи память", "список памяти", "мои факты", "show memory",
    "память", "моя память", "список фактов", "все факты"
]
CLEAR_TRIGGERS = [
    "забудь всё", "забудь все", "очисти память", "clear memory", "forget all"
]

_SAVE_PATTERN_STR = (
    r"^\s*(?:" + "|".join(re.escape(t) for t in sorted(SAVE_TRIGGERS, key=len, reverse=True)) + r")"
    r"(?:[:,\s\-–—]+|\s+что\s+|\s+то,\s*что\s*|\s+)+(.*)$"
)
_SAVE_REGEX = re.compile(_SAVE_PATTERN_STR, re.IGNORECASE | re.DOTALL)
_STANDALONE_SAVE_REGEX = re.compile(
    r"^\s*(?:" + "|".join(re.escape(t) for t in sorted(SAVE_TRIGGERS, key=len, reverse=True)) + r")\s*[:,\-–—]?\s*$",
    re.IGNORECASE
)
_VIEW_PATTERN_STR = r"^\s*(?:" + "|".join(re.escape(t) for t in sorted(VIEW_TRIGGERS, key=len, reverse=True)) + r")[\s?!.]*$"
_VIEW_REGEX = re.compile(_VIEW_PATTERN_STR, re.IGNORECASE)
_CLEAR_PATTERN_STR = r"^\s*(?:" + "|".join(re.escape(t) for t in sorted(CLEAR_TRIGGERS, key=len, reverse=True)) + r")[\s.!]*(.*)$"
_CLEAR_REGEX = re.compile(_CLEAR_PATTERN_STR, re.IGNORECASE)
_DELETE_ID_REGEX = re.compile(r"^\s*(?:забудь(?:\s+факт)?|удали(?:\s+факт)?|forget(?:\s+fact)?|delete(?:\s+fact)?)\s*#?(\d+)[\s.]*$", re.IGNORECASE)
_DELETE_KEYWORD_REGEX = re.compile(r"^\s*(?:забудь(?:\s+факт)?|удали(?:\s+факт)?|forget(?:\s+fact)?|delete(?:\s+fact)?)\s+(.+)$", re.IGNORECASE | re.DOTALL)

def normalize_fact_text(text: str) -> str:
    """Normalizes 1st person pronouns into 3rd person to prevent model identity confusion."""
    cleaned = text.strip()
    cleaned = re.sub(r'^(?:я\s+|мне\s+|меня\s+)', 'Пользователь ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^(?:мой\s+|мое\s+|моё\s+)', 'Проект/предпочтение пользователя: ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+я\s+', ' пользователь ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+мне\s+', ' пользователю ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+мой\s+', ' пользователя ', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def parse_memory_trigger(text: str) -> Tuple[bool, Optional[str]]:
    if not text:
        return False, None
    cleaned = text.strip()
    match = _SAVE_REGEX.match(cleaned)
    if match:
        fact = match.group(1).strip()
        fact = re.sub(r"^(?:что|то,\s*что)\s+", "", fact, flags=re.IGNORECASE).strip()
        if (fact.startswith('"') and fact.endswith('"')) or (fact.startswith("'") and fact.endswith("'")):
            fact = fact[1:-1].strip()
        return True, fact
    if _STANDALONE_SAVE_REGEX.match(cleaned):
        return True, ""
    return False, None

class MemoryDB:
    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else MEMORY_DB_PATH
        self._init_db()
        self._sync_checked = False

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fact TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        text TEXT NOT NULL,
                        remind_at TEXT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_status_time ON reminders(status, remind_at);")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rp_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        fact TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_memory_sess ON rp_memory(session_id);")
                conn.execute("CREATE VIEW IF NOT EXISTS memories AS SELECT id, fact, created_at FROM user_memory;")
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize MemoryDB schema: {e}")

    @property
    def table(self):
        """Возвращает дескриптор таблицы 'facts' LanceDB с идемпотентной синхронизацией."""
        _, facts_t, _ = _get_embedding_engine()
        if facts_t and not getattr(self, "_sync_checked", False):
            self._sync_checked = True
            try:
                self.sync_and_reindex_facts()
            except Exception as e_sync:
                logger.warning(f"[MemoryDB] Фоновая синхронизация фактов не удалась: {e_sync}")
        return facts_t if facts_t else None

    def sync_and_reindex_facts(self) -> int:
        """
        Идемпотентная синхронизация LanceDB ↔ SQLite:
        1. Удаляет осиротевшие записи LanceDB, отсутствующие в SQLite `user_memory`.
        2. Переиндексирует живые записи из SQLite L2-нормализованными векторами,
           устраняя рассинхрон и смесь сырых/нормализованных векторов.
        Возвращает количество синхронизированных фактов.
        """
        try:
            embed_model, _, _ = _get_embedding_engine()
            if not embed_model:
                return 0

            with self._get_connection() as conn:
                cursor = conn.execute("SELECT id, fact FROM user_memory ORDER BY id ASC;")
                sqlite_facts = cursor.fetchall()

            _, facts_t, _ = _get_embedding_engine()
            if not facts_t:
                return 0

            with _LANCE_LOCK:
                try:
                    rows = facts_t.to_arrow().to_pylist()
                except Exception as e_arrow:
                    logger.debug(f"[MemoryDB.Sync] Не удалось прочитать таблицу LanceDB: {e_arrow}")
                    rows = []

                sqlite_ids = {str(r["id"]) for r in sqlite_facts}
                non_init_lance = [r for r in rows if r.get("id") != "init_id"]
                lance_ids = {str(r.get("id")) for r in non_init_lance}

                # Проверяем, нужна ли миграция:
                # 1) Есть ли записи в LanceDB, которых нет в SQLite (осиротевшие)
                # 2) Есть ли записи из SQLite, которых нет в LanceDB
                # 3) Имеют ли векторы ненормализованную длину (> 1.05)
                needs_sync = False
                if lance_ids != sqlite_ids:
                    needs_sync = True
                else:
                    for r in non_init_lance:
                        v = r.get("vector")
                        if v:
                            arr_v = np.array(v, dtype=np.float32)
                            norm_v = np.linalg.norm(arr_v)
                            if abs(norm_v - 1.0) > 0.05:
                                needs_sync = True
                                break

                if not needs_sync:
                    return len(sqlite_facts)

                logger.info(
                    f"[MemoryDB.Sync] Обнаружен рассинхрон или ненормализованные векторы: "
                    f"LanceDB={len(non_init_lance)}, SQLite={len(sqlite_facts)}. Выполняется синхронизация..."
                )

                # Очищаем таблицу LanceDB безопасным синтаксисом (одинарные кавычки)
                try:
                    facts_t.delete("id != 'init_id'")
                except Exception as e_del:
                    logger.warning(f"[MemoryDB.Sync] Предупреждение при очистке LanceDB: {e_del}")

                # Перевставляем все живые факты из SQLite с L2-нормализацией
                reindexed_items = []
                for row in sqlite_facts:
                    f_id = str(row["id"])
                    f_text = str(row["fact"]).strip()
                    if not f_text:
                        continue
                    raw_v = list(embed_model.embed([f_text]))[0]
                    norm_v = _normalize_l2(raw_v.tolist() if hasattr(raw_v, "tolist") else raw_v)
                    reindexed_items.append({"id": f_id, "text": f_text, "vector": norm_v})

                if reindexed_items:
                    facts_t.add(reindexed_items)

                try:
                    if hasattr(facts_t, "optimize"):
                        facts_t.optimize(cleanup_older_than=timedelta(0), delete_unverified=True)
                except Exception as e_opt:
                    logger.debug(f"[MemoryDB.Sync] Оптимизация LanceDB после синхронизации: {e_opt}")

                logger.info(
                    f"[MemoryDB.Sync] Синхронизация успешно завершена: {len(reindexed_items)} "
                    f"фактов переиндексировано с L2-нормализацией."
                )
                return len(reindexed_items)
        except Exception as e:
            logger.warning(f"[MemoryDB.Sync] Ошибка синхронизации LanceDB ↔ SQLite: {e}")
            return 0

    @property
    def rp_table(self):
        """Возвращает дескриптор таблицы 'rp_memories' LanceDB."""
        _, _, rp_t = _get_embedding_engine()
        return rp_t if rp_t else None

    def optimize_tables(self):
        """Выполняет объединение мелких фрагментов и очистку устаревших версий."""
        for table_name, table in [("facts", self.table), ("rp_memories", self.rp_table)]:
            if table is None:
                continue
            try:
                with _LANCE_LOCK:
                    optimized = False
                    if hasattr(table, "optimize"):
                        try:
                            table.optimize(cleanup_older_than=timedelta(0), delete_unverified=True)
                            optimized = True
                        except Exception as e_opt:
                            logger.debug(f"[LanceDB] table.optimize fallback for '{table_name}': {e_opt}")
                    if not optimized:
                        if hasattr(table, "compact_files"):
                            try:
                                table.compact_files()
                            except Exception as e_compact:
                                logger.debug(f"[LanceDB] compact_files fallback for '{table_name}': {e_compact}")
                        if hasattr(table, "cleanup_old_versions"):
                            try:
                                table.cleanup_old_versions(older_than=timedelta(0), delete_unverified=True)
                            except Exception:
                                try:
                                    table.cleanup_old_versions()
                                except Exception as e_clean:
                                    logger.debug(f"[LanceDB] cleanup_old_versions fallback for '{table_name}': {e_clean}")

                logger.info(f"Таблица LanceDB '{table_name}' успешно оптимизирована.")
            except Exception as e:
                # Логируем, но не прерываем работу приложения
                logger.warning(f"Ошибка при компактизации таблицы LanceDB '{table_name}': {e}")

    def cleanup(self):
        """Штатная очистка и оптимизация ресурсов памяти."""
        self.optimize_tables()

    def close(self):
        """Штатное закрытие MemoryDB с оптимизацией таблиц."""
        self.cleanup()

    def add_rp_memory(self, session_id: str, text: str) -> int:
        """
        Сохраняет сюжетную веху в векторную память конкретной сессии.
        Проверяет дедупликацию и удерживает лимит до 100 воспоминаний на сессию.
        """
        if not session_id or not text or not text.strip():
            return -1

        clean_text = text.strip()
        clean_lower = clean_text.lower()

        # 1. Проверка дубликатов в SQLite для этой сессии
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, fact FROM rp_memory WHERE session_id = ? ORDER BY id DESC;",
                    (session_id,)
                )
                existing = cursor.fetchall()
                for ef in existing:
                    f_low = ef["fact"].lower()
                    if clean_lower in f_low or f_low in clean_lower:
                        return ef["id"]

                # Скользящий лимит: максимум 100 воспоминаний на диалог (для длинных сюжетов)
                if len(existing) >= 100:
                    oldest_id = existing[-1]["id"]
                    conn.execute("DELETE FROM rp_memory WHERE id = ?;", (oldest_id,))
                    _, _, rp_table = _get_embedding_engine()
                    if rp_table:
                        try:
                            safe_oldest = str(oldest_id).replace("'", "''")
                            with _LANCE_LOCK:
                                rp_table.delete(f"id = '{safe_oldest}'")
                        except Exception as e_old:
                            logger.warning(f"[RPMemory] Ошибка удаления устаревшего воспоминания: {e_old}")
        except Exception as e:
            logger.error(f"[RPMemory] Ошибка проверки дубликатов: {e}")

        # 2. Семантическая проверка в LanceDB
        try:
            embed_model, _, rp_table = _get_embedding_engine()
            if embed_model and rp_table:
                safe_sid = str(session_id).replace("'", "''")
                with _LANCE_LOCK:
                    vec = list(embed_model.embed([clean_text]))[0].tolist()
                    sim_res = rp_table.search(vec).where(f"session_id = '{safe_sid}'").limit(1).to_list()
                if sim_res:
                    top_m = sim_res[0]
                    dist = top_m.get("_distance", 1.0)
                    if top_m.get("id") != "init_id" and dist < 0.20:
                        return int(top_m.get("id")) if top_m.get("id", "").isdigit() else -1
        except Exception:
            pass

        # 3. Сохранение в SQLite
        row_id = -1
        date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "INSERT INTO rp_memory (session_id, fact, created_at) VALUES (?, ?, ?);",
                    (session_id, clean_text, date_str)
                )
                conn.commit()
                row_id = cur.lastrowid
                logger.info(f"[NeonRoute.RPMemory] Сохранена сюжетная веха [Сессия {session_id[:8]} | ID {row_id}]: «{clean_text}»")
        except Exception as e:
            logger.error(f"[RPMemory] Ошибка сохранения в SQLite: {e}")
            return -1

        # 4. Сохранение в LanceDB
        try:
            embed_model, _, rp_table = _get_embedding_engine()
            if embed_model and rp_table:
                with _LANCE_LOCK:
                    vec = list(embed_model.embed([clean_text]))[0].tolist()
                    rp_table.add([{
                        "id": str(row_id),
                        "session_id": str(session_id),
                        "text": clean_text,
                        "vector": vec,
                        "timestamp": str(time.time())
                    }])
        except Exception as e:
            logger.debug(f"[RPMemory] LanceDB insert error: {e}")

        _maybe_cleanup_lance_tables()
        return row_id

    def search_rp_memories(self, session_id: str, query: str, top_k: int = 4, max_distance: float = 0.42) -> List[str]:
        """
        Ищет семантически релевантные воспоминания строго внутри активной сессии.
        Логирует совпадения и дистанции.
        """
        if not session_id or not query or not query.strip():
            return []

        try:
            embed_model, _, rp_table = _get_embedding_engine()
            if embed_model and rp_table:
                safe_sid = str(session_id).replace("'", "''")
                with _LANCE_LOCK:
                    query_vec = list(embed_model.embed([query.strip()]))[0].tolist()
                    res = rp_table.search(query_vec).where(f"session_id = '{safe_sid}'").limit(top_k * 2).to_list()
                
                relevant = []
                for r in res:
                    if r.get("id") == "init_id":
                        continue
                    dist = r.get("_distance", 1.0)
                    if dist <= max_distance:
                        relevant.append((r["text"], dist))

                if relevant:
                    # Сортировка по минимальной дистанции и срез top_k
                    relevant.sort(key=lambda x: x[1])
                    top_matches = relevant[:top_k]
                    
                    log_hits = "\n".join(f"  • (dist={d:.3f}): «{txt}»" for txt, d in top_matches)
                    logger.info(
                        f"[NeonRoute.RPMemory] [Recall-Hit] Сессия [{session_id[:8]}]: "
                        f"найдено {len(top_matches)} воспоминаний по запросу «{query[:40]}...»:\n{log_hits}"
                    )
                    return [txt for txt, _ in top_matches]
                else:
                    logger.debug(f"[NeonRoute.RPMemory] Сессия [{session_id[:8]}]: воспоминаний не найдено (порог <= {max_distance}).")
        except Exception as e:
            logger.debug(f"[RPMemory] Ошибка поиска: {e}")

        return []

    def clear_rp_session_memory(self, session_id: str):
        """Очищает всю память сессии при удалении диалога."""
        if not session_id:
            return
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM rp_memory WHERE session_id = ?;", (session_id,))
                conn.commit()
            _, _, rp_table = _get_embedding_engine()
            if rp_table:
                safe_sid = str(session_id).replace("'", "''")
                with _LANCE_LOCK:
                    rp_table.delete(f"session_id = '{safe_sid}'")
            logger.info(f"[NeonRoute.RPMemory] Очищена память сессии [{session_id[:8]}].")
            _maybe_cleanup_lance_tables()
        except Exception as e:
            logger.debug(f"[RPMemory] Ошибка очистки сессии: {e}")

    def add_fact(self, text: str) -> int:
        """
        Adds a fact with automatic 3rd person normalization,
        exact duplicate check, and semantic similarity check.
        """
        raw_fact = text.strip() if text else ""
        if not raw_fact:
            return -1

        clean_fact = normalize_fact_text(raw_fact)
        clean_fact_lower = clean_fact.lower()

        # 1. Exact duplicate check in SQLite
        existing_facts = self.get_all_facts()
        for ef in existing_facts:
            ef_lower = ef["fact"].strip().lower()
            if ef_lower == clean_fact_lower or clean_fact_lower in ef_lower or ef_lower in clean_fact_lower:
                logger.info(f"Duplicate fact ignored (exact/substring match): '{clean_fact}' matches #{ef['id']}")
                return ef["id"]

        # 2. Semantic similarity check via LanceDB (cosine distance < 0.15 means near identical)
        try:
            embed_model, table, _ = _get_embedding_engine()
            if embed_model and table:
                with _LANCE_LOCK:
                    raw_v = list(embed_model.embed([clean_fact]))[0]
                    vec = _normalize_l2(raw_v.tolist() if hasattr(raw_v, "tolist") else raw_v)
                    sim_res = table.search(vec).metric("cosine").limit(1).to_list()
                if sim_res:
                    top_match = sim_res[0]
                    dist = top_match.get("_distance", 1.0)
                    if top_match.get("id") != "init_id" and dist < 0.15:
                        logger.info(f"Semantic duplicate ignored (cosine dist={dist:.3f}): '{clean_fact}' ~ '{top_match.get('text')}'")
                        return int(top_match.get("id")) if top_match.get("id", "").isdigit() else -1
        except Exception as e:
            logger.debug(f"Semantic deduplication skipped: {e}")

        # 3. Insert new unique fact into SQLite
        date_str = datetime.now().strftime("%d.%m.%Y")
        row_id = -1
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("INSERT INTO user_memory (fact, created_at) VALUES (?, ?);", (clean_fact, date_str))
                conn.commit()
                row_id = cursor.lastrowid
                logger.info(f"Saved new unique fact [ID: {row_id}]: '{clean_fact}'")
        except Exception as e:
            logger.error(f"Error saving fact to SQLite: {e}")
            return -1

        # 4. Insert into LanceDB
        try:
            embed_model, table, _ = _get_embedding_engine()
            if embed_model and table:
                with _LANCE_LOCK:
                    raw_v = list(embed_model.embed([clean_fact]))[0]
                    vec = _normalize_l2(raw_v.tolist() if hasattr(raw_v, "tolist") else raw_v)
                    table.add([{"id": str(row_id), "text": clean_fact, "vector": vec}])
            _maybe_cleanup_lance_tables()
        except Exception as e:
            logger.warning(f"LanceDB insert error: {e}")

        return row_id

    def search_relevant_facts(self, query: str, top_k: int = 5, max_distance: float = 0.64) -> List[str]:
        """
        Возвращает только семантически релевантные факты (порог косинусной дистанции max_distance=0.64).
        Если совпадений нет, возвращает пустой список, исключая галлюцинации контекста.
        """
        if not query or not query.strip():
            return []
        try:
            embed_model, table, _ = _get_embedding_engine()
            if embed_model and table:
                with _LANCE_LOCK:
                    raw_v = list(embed_model.embed([query.strip()]))[0]
                    query_vec = _normalize_l2(raw_v.tolist() if hasattr(raw_v, "tolist") else raw_v)
                    res = table.search(query_vec).metric("cosine").limit(top_k).to_list()
                # Фильтрация по косинусной дистанции: отсекаем семантический шум
                valid_res = [r for r in res if r.get("id") != "init_id"]
                best_dist = min((float(r.get("_distance", 1.0)) for r in valid_res), default=1.0)
                relevant = [
                    r["text"] for r in valid_res 
                    if float(r.get("_distance", 1.0)) <= max_distance
                ]
                if relevant:
                    logger.info(
                        f"[MemoryDB] Поиск: найдено {len(relevant)} факт(ов), best_dist={best_dist:.3f} по запросу «{query[:40]}...»"
                    )
                else:
                    logger.info(
                        f"[MemoryDB] Поиск: найдено 0 факт(ов), best_dist={best_dist:.3f} (порог {max_distance}) по запросу «{query[:40]}...»"
                    )
                return relevant
            else:
                logger.warning(
                    f"[MemoryDB] Движок памяти недоступен для поиска: embed_model={bool(embed_model)}, table={bool(table)}"
                )
        except Exception as e:
            logger.warning(f"Search error in LanceDB: {e}")

        return []

    def get_all_facts(self) -> List[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT id, fact, created_at FROM user_memory ORDER BY id ASC;")
                return [{"id": r["id"], "fact": r["fact"], "date": r["created_at"]} for r in cursor.fetchall() if r["fact"]]
        except Exception as e:
            logger.error(f"Error fetching facts: {e}")
            return []

    def get_all_records(self) -> List[Dict[str, Any]]:
        return self.get_all_facts()

    def get_all_memories_formatted(self) -> str:
        """
        Форматирование вывода списка фактов строго от 1 по порядку
        без вывода технических ID базы.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT id, fact FROM memories ORDER BY id ASC;")
                except sqlite3.OperationalError:
                    cursor.execute("SELECT id, fact FROM user_memory ORDER BY id ASC;")
                rows = cursor.fetchall()
                lines = [f"{idx}. {row['fact']}" for idx, row in enumerate(rows, start=1)]
                return "\n".join(lines) if lines else "Я пока ничего о вас не знаю."
        except Exception as e:
            logger.error(f"Error fetching formatted memories: {e}")
            return "Я пока ничего о вас не знаю."

    def delete_fact_by_number(self, n: int | str) -> str:
        """
        Детерминированное удаление по порядковому номеру через OFFSET
        с fallback-поиском по id.
        """
        try:
            n_int = int(n)
        except (ValueError, TypeError):
            return f"Факт под номером {n} не найден."

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                row = None
                table_name = "memories"
                try:
                    conn.execute("SELECT 1 FROM memories LIMIT 1;")
                except sqlite3.OperationalError:
                    table_name = "user_memory"

                # 1. Сначала ищем запись по порядковому номеру (OFFSET N-1)
                if n_int >= 1:
                    cursor.execute(
                        f"SELECT id, fact FROM {table_name} ORDER BY id ASC LIMIT 1 OFFSET ?",
                        (n_int - 1,)
                    )
                    row = cursor.fetchone()

                # 2. Если запись по смещению не найдена, пробуем прямой поиск по id (fallback)
                if not row:
                    cursor.execute(f"SELECT id, fact FROM {table_name} WHERE id = ?", (n_int,))
                    row = cursor.fetchone()

                if row:
                    target_id = row["id"]
                    deleted_fact = row["fact"]
                    # Удаляем из SQLite и LanceDB по target_id
                    self.delete_fact_by_id(target_id)
                    return f"Забыла: «{deleted_fact}»."
                else:
                    return f"Факт под номером {n} не найден."
        except Exception as e:
            logger.error(f"Error deleting fact by number {n}: {e}")
            return f"Факт под номером {n} не найден."

    def delete_fact_by_id(self, fact_id: int) -> bool:
        deleted = False
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("DELETE FROM user_memory WHERE id = ?;", (fact_id,))
                conn.commit()
                deleted = cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting fact #{fact_id}: {e}")

        try:
            _, table, _ = _get_embedding_engine()
            if table:
                safe_id = str(fact_id).replace("'", "''")
                with _LANCE_LOCK:
                    table.delete(f"id = '{safe_id}'")
        except Exception as e_del:
            logger.warning(f"Error deleting fact #{fact_id} from LanceDB: {e_del}")
        return deleted

    def delete_fact(self, fact_id: int) -> bool:
        return self.delete_fact_by_id(fact_id)

    def delete_fact_by_keyword(self, keyword: str) -> int:
        clean_kw = keyword.strip() if keyword else ""
        if not clean_kw:
            return 0
        clean_kw = re.sub(r"^(?:про|о|об|about)\s+", "", clean_kw, flags=re.IGNORECASE).strip().strip('"\'')
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT id FROM user_memory WHERE fact LIKE ?;", (f"%{clean_kw}%",))
                m_ids = [r["id"] for r in cursor.fetchall()]
                cursor = conn.execute("DELETE FROM user_memory WHERE fact LIKE ?;", (f"%{clean_kw}%",))
                conn.commit()
                count = cursor.rowcount

            _, table, _ = _get_embedding_engine()
            if table and m_ids:
                with _LANCE_LOCK:
                    for mid in m_ids:
                        try:
                            safe_mid = str(mid).replace("'", "''")
                            table.delete(f"id = '{safe_mid}'")
                        except Exception as e_kw:
                            logger.warning(f"Error deleting fact #{mid} by keyword from LanceDB: {e_kw}")
            return count
        except Exception as e:
            logger.error(f"Error deleting by keyword: {e}")
            return 0

    def clear_all(self) -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM user_memory;")
                try:
                    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'user_memory';")
                except Exception:
                    pass
                conn.commit()

            _, table, _ = _get_embedding_engine()
            if table:
                with _LANCE_LOCK:
                    table.delete("id != 'init_id'")
            _maybe_cleanup_lance_tables()
            return True
        except Exception as e:
            logger.error(f"Error clearing memory: {e}")
            return False

    def clear_all_facts(self) -> bool:
        return self.clear_all()

    def add_reminder(self, text: str, remind_at_iso: str) -> int:
        """Добавляет напоминание в очередь планировщика."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "INSERT INTO reminders (text, remind_at, status, created_at) VALUES (?, ?, 'pending', ?);",
                    (text.strip(), remind_at_iso.strip(), now_str)
                )
                conn.commit()
                r_id = cursor.lastrowid
                logger.info(f"Saved new reminder [ID: {r_id}] for {remind_at_iso}: '{text}'")
                return r_id
        except Exception as e:
            logger.error(f"Error adding reminder: {e}")
            return -1

    def get_due_reminders(self) -> List[Dict[str, Any]]:
        """Возвращает список напоминаний, срок которых наступил."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, text, remind_at FROM reminders WHERE status = 'pending' AND remind_at <= ? ORDER BY remind_at ASC;",
                    (now_str,)
                )
                rows = cursor.fetchall()
                return [{"id": r["id"], "text": r["text"], "remind_at": r["remind_at"]} for r in rows]
        except Exception as e:
            logger.error(f"Error getting due reminders: {e}")
            return []

    def mark_reminder_done(self, reminder_id: int) -> bool:
        """Помечает напоминание как выполненное."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("UPDATE reminders SET status = 'done' WHERE id = ?;", (reminder_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error marking reminder done #{reminder_id}: {e}")
            return False

    def get_all_reminders(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Возвращает последние напоминания."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, text, remind_at, status, created_at FROM reminders ORDER BY id DESC LIMIT ?;",
                    (limit,)
                )
                return [{"id": r["id"], "text": r["text"], "remind_at": r["remind_at"], "status": r["status"]} for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting all reminders: {e}")
            return []

memory_db = MemoryDB()

# Автоматическая оптимизация таблиц памяти при штатном завершении процесса
try:
    atexit.register(memory_db.close)
except Exception:
    pass

def optimize_tables(db: Optional[MemoryDB] = None):
    """Выполняет оптимизацию таблиц для указанного или глобального экземпляра MemoryDB."""
    active_db = db or memory_db
    active_db.optimize_tables()

def get_all_memories_formatted(db: Optional[MemoryDB] = None) -> str:
    active_db = db or memory_db
    return active_db.get_all_memories_formatted()

def delete_fact_by_number(n: int | str, db: Optional[MemoryDB] = None) -> str:
    active_db = db or memory_db
    return active_db.delete_fact_by_number(n)

def handle_memory_command(text: str, db: Optional[MemoryDB] = None) -> Tuple[bool, Optional[str]]:
    if not text:
        return False, None
    active_db = db or memory_db
    cleaned = text.strip()

    if _VIEW_REGEX.match(cleaned):
        return True, active_db.get_all_memories_formatted()

    if _CLEAR_REGEX.match(cleaned):
        active_db.clear_all()
        return True, "Очистила всю память о тебе."

    id_match = _DELETE_ID_REGEX.match(cleaned)
    if id_match:
        n = id_match.group(1)
        reply = active_db.delete_fact_by_number(n)
        return True, reply

    kw_match = _DELETE_KEYWORD_REGEX.match(cleaned)
    if kw_match:
        raw_kw = kw_match.group(1).strip()
        if not any(raw_kw.lower().startswith(t) for t in SAVE_TRIGGERS):
            clean_kw = re.sub(r"^(?:про|о|об|about)\s+", "", raw_kw, flags=re.IGNORECASE).strip().strip('"\'')
            if clean_kw:
                cnt = active_db.delete_fact_by_keyword(clean_kw)
                if cnt > 0:
                    return True, f"Удалила из памяти {cnt} факт(ов), содержащих «{clean_kw}»."
                return True, f"Не нашла в памяти записей, содержащих «{clean_kw}»."

    is_save, fact_text = parse_memory_trigger(cleaned)
    if is_save:
        if fact_text and fact_text.strip():
            clean_content = re.sub(r"^[\s.]+|[\s.]+$", "", fact_text)
            if not clean_content:
                return True, "Пожалуйста, укажите факт. Например: «Запомни, меня зовут...»"
            row_id = active_db.add_fact(clean_content)
            if row_id == -1:
                return True, "Этот факт уже есть в моей памяти. Дубликат не сохранен."
            return True, "Записала в долговременную память. Буду учитывать в ответах."
        return True, "Пожалуйста, укажите факт. Например: «Запомни, меня зовут...»"

    return False, None

# Фоновый прогрев FastEmbed и LanceDB при запуске (устраняет микрофриз первого сообщения)
import threading

def warmup_memory_engine(delay: float = 6.0):
    """
    Централизованный фоновый прогрев FastEmbed и LanceDB с отсрочкой (Deferred Warmup),
    чтобы исключить конкуренцию за CPU и шину памяти во время загрузки весов LLM.
    """
    def _run():
        if delay > 0:
            time.sleep(delay)
        try:
            _get_embedding_engine()
            if "memory_db" in globals() and globals()["memory_db"] is not None:
                globals()["memory_db"].sync_and_reindex_facts()
        except Exception as e:
            logger.debug(f"Async warmup exception: {e}")

    threading.Thread(target=_run, daemon=True, name="FastEmbedWarmup").start()

