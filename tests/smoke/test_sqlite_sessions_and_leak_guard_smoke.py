"""
Смок-тест хранилища сессий SQLite WAL для 3 режимов (Assistant, Roleplay, Engineer)
и сторожевой детектор утечек тестовых сессий в боевое окружение.
"""
import os
import json
import sqlite3
from pathlib import Path
import pytest

import config
import services.session_manager as sm_mod
from services.session_manager import SessionManager
from services.memory_db import memory_db


def test_01_three_modes_sqlite_wal_recording_and_fidelity():
    """
    Проверяет, что чаты всех 3 режимов (Assistant, Roleplay, Engineer) записываются
    по новому способу в SQLite WAL (таблицы chat_sessions и chat_messages) + быстрое зеркало,
    сохраняя все специфичные для каждого режима поля без потерь.
    """
    # 1. Чат режима АССИСТЕНТ (с чипами базы знаний RAG и блоком рассуждений <think>)
    sess_assistant = SessionManager.create_session(
        mode="assistant",
        title="Ассистент: Квантовый Аудит",
        model_id="qwen2.5-7b-instruct",
    )
    sess_assistant["metadata"] = {"active_assistant_skill": "analyst_logic"}
    sess_assistant["messages"].extend([
        {
            "id": "msg_ast_u1",
            "sender": "User",
            "role": "user",
            "text": "Проверь артикул охлаждения NR-9090.",
            "is_user": True,
        },
        {
            "id": "msg_ast_a1",
            "sender": "Элария",
            "role": "assistant",
            "text": "Деталь NR-9090 работает в штатном режиме.",
            "is_user": False,
            "thought": "Сверяю реестр документации RAG...",
            "knowledge_sources": [{"file": "manual_spec.md", "score": 0.92}],
        },
    ])

    # 2. Чат режима РОЛЕВОЙ ЧАТ (с карточкой персонажа, Visual Intent и ЛитРПГ статами)
    sess_roleplay = SessionManager.create_session(
        mode="roleplay",
        title="РП: Цитадель Ночи",
        custom_system_prompt="Ты — Хранитель Цитадели.",
    )
    sess_roleplay["character_data"] = {
        "id": "char_keeper_01",
        "name": "Веспера",
        "archetype": "System",
        "visual_dna": "1girl, silver hair, obsidian armor",
    }
    sess_roleplay["custom_depth_anchor"] = "[Depth-0: Включать блок <stats> в конце хода]"
    sess_roleplay["messages"].extend([
        {
            "id": "msg_rp_u1",
            "sender": "Игрок",
            "role": "user",
            "text": "Открываю врата зала.",
            "is_user": True,
        },
        {
            "id": "msg_rp_a1",
            "sender": "Веспера",
            "role": "assistant",
            "text": "Врата медленно поддаются.\n<stats>\nЛокация: Тронный Зал\nУровень: 5\n</stats>",
            "is_user": False,
            "image_path": "outputs/citadel_gate.png",
            "visual_intent": {"target": "COMPANION", "scene_type": "exploration"},
        },
    ])

    # 3. Чат режима ИНЖЕНЕР (с привязкой артефактов песочницы и скиллом TDD)
    sess_engineer = SessionManager.create_session(
        mode="engineer",
        title="Инженер: Разработка Парсера",
        model_id="qwen2.5-coder-7b-instruct",
        is_model_locked=True,
    )
    sess_engineer["type"] = "engineering"
    sess_engineer["metadata"] = {"active_engineer_skill": "pure_logic_tdd"}
    sess_engineer["messages"].extend([
        {
            "id": "msg_eng_u1",
            "sender": "User",
            "role": "user",
            "text": "Напиши модуль парсинга с тестами pytest.",
            "is_user": True,
        },
        {
            "id": "msg_eng_a1",
            "sender": "Инженер",
            "role": "assistant",
            "text": "Модуль parser.py создан и прошел 4 теста (exit_code == 0).",
            "is_user": False,
            "file_paths": ["workspace/projects/parser.py", "workspace/projects/test_parser.py"],
            "generation_params": {"archetype": "pure_logic_tdd", "passed_tdd": True},
        },
    ])

    three_sessions = [sess_assistant, sess_roleplay, sess_engineer]
    for s in three_sessions:
        saved_path = SessionManager.save_session(s, trigger_memory=False)
        assert saved_path is not None and saved_path.exists()
        assert "pytest_airgap_sandbox" in str(saved_path)

    # Проверяем напрямую в SQLite базе (WAL), что все 3 сессии и все 6 сообщений записаны
    with SessionManager._get_sqlite_conn() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert str(journal_mode).lower() == "wal"

        for s in three_sessions:
            sid = s["id"]
            row = conn.execute("SELECT id, title, mode, header_json FROM chat_sessions WHERE id = ?", (sid,)).fetchone()
            assert row is not None, f"Сессия режима {s['mode']} не попала в таблицу chat_sessions!"
            assert row["title"] == s["title"]
            assert row["mode"] == s["mode"]

            msg_rows = conn.execute(
                "SELECT message_idx, msg_id, role, text, raw_msg_json FROM chat_messages WHERE session_id = ? ORDER BY message_idx ASC",
                (sid,),
            ).fetchall()
            assert len(msg_rows) == 2, f"Ожидалось 2 сообщения в SQLite для режима {s['mode']}, найдено {len(msg_rows)}"

    # Полностью очищаем оперативный кэш и проверяем чтение из SQLite WAL
    SessionManager._sessions_cache.clear()
    SessionManager._current_session = None

    loaded_ast = SessionManager.load_session(sess_assistant["id"])
    assert loaded_ast["metadata"]["active_assistant_skill"] == "analyst_logic"
    assert loaded_ast["messages"][1]["thought"] == "Сверяю реестр документации RAG..."
    assert loaded_ast["messages"][1]["knowledge_sources"][0]["file"] == "manual_spec.md"

    loaded_rp = SessionManager.load_session(sess_roleplay["id"])
    assert loaded_rp["character_data"]["name"] == "Веспера"
    assert loaded_rp["custom_depth_anchor"] == "[Depth-0: Включать блок <stats> в конце хода]"
    assert loaded_rp["messages"][1]["visual_intent"]["scene_type"] == "exploration"

    loaded_eng = SessionManager.load_session(sess_engineer["id"])
    assert loaded_eng["type"] == "engineering"
    assert loaded_eng["is_model_locked"] is True
    assert loaded_eng["messages"][1]["generation_params"]["passed_tdd"] is True

    # Проверяем, что list_sessions возвращает все 3 сессии из SQLite
    listed_ids = {item["id"] for item in SessionManager.list_sessions()}
    assert {sess_assistant["id"], sess_roleplay["id"], sess_engineer["id"]}.issubset(listed_ids)


def test_02_external_json_edit_syncs_into_sqlite_wal():
    """
    Проверяет, что если внешний процесс (например, mobile_server) обновил файл chat_<id>.json,
    SessionManager.load_session обнаруживает новый st_mtime и синхронизирует изменения в SQLite.
    """
    sess = SessionManager.create_session(mode="assistant", title="Проверка Внешней Синхронизации")
    sid = sess["id"]
    json_path = SessionManager.save_session(sess)
    assert json_path and json_path.exists()

    # Имитируем внешнюю правку JSON-файла с новой репликой и сдвигом mtime вперед на 5 секунд
    on_disk = json.loads(json_path.read_text(encoding="utf-8"))
    on_disk["messages"].append({
        "id": "msg_ext_mobile",
        "sender": "MobileUser",
        "role": "user",
        "text": "Реплика с мобильного клиента через BFF!",
        "is_user": True,
    })
    json_path.write_text(json.dumps(on_disk, ensure_ascii=False), encoding="utf-8")
    future_mtime = json_path.stat().st_mtime + 5.0
    os.utime(json_path, (future_mtime, future_mtime))

    SessionManager._sessions_cache.clear()
    reloaded = SessionManager.load_session(sid)
    assert len(reloaded["messages"]) == 1
    assert reloaded["messages"][0]["id"] == "msg_ext_mobile"

    # Убеждаемся, что внешняя реплика теперь зацементирована и в SQLite
    with SessionManager._get_sqlite_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (sid,)).fetchone()[0]
        assert cnt == 1


def test_03_cross_test_leak_detector_and_production_purity():
    """
    Проверяет, что:
    1) Текущие пути записи SessionManager и memory_db строго изолированы в pytest_airgap_sandbox.
    2) В реальной боевой базе C:/NeonRoute/data/memory.db и папке C:/NeonRoute/history
       нет утекших тестовых сессий от других тестов (с маркерами test_, smoke_, pytest_airgap).
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    prod_history_dir = project_root / "history"
    prod_memory_db = project_root / "data" / "memory.db"

    # 1. Проверка строгой изоляции активных констант во время теста
    assert "pytest_airgap_sandbox" in str(sm_mod.HISTORY_DIR), (
        f"УТЕЧКА КОНФИГУРАЦИИ: SessionManager.HISTORY_DIR смотрит мимо песочницы: {sm_mod.HISTORY_DIR}"
    )
    assert "pytest_airgap_sandbox" in str(memory_db.db_path), (
        f"УТЕЧКА КОНФИГУРАЦИИ: memory_db.db_path смотрит мимо песочницы: {memory_db.db_path}"
    )

    # 2. Сканируем боевую папку history/ на предмет просочившихся тестовых файлов от других тестов
    suspicious_markers = (
        "smoke test chat",
        "pytest_airgap",
        "test_sess",
        "ассистент: квантовый аудит",
        "рп: цитадель ночи",
        "инженер: разработка парсера",
    )
    leaked_json_files = []
    if prod_history_dir.exists():
        for p in prod_history_dir.glob("chat_*.json"):
            if p.stem.startswith("chat_test_") or "test_sess" in p.name:
                leaked_json_files.append(p.name)
                continue
            try:
                raw_head = p.read_text(encoding="utf-8", errors="ignore")[:600].lower()
                if any(m in raw_head for m in suspicious_markers):
                    leaked_json_files.append(p.name)
            except Exception:
                pass

    assert not leaked_json_files, (
        f"ОБНАРУЖЕНА УТЕЧКА ОТ ДРУГИХ ТЕСТОВ в боевую папку history/: {leaked_json_files}"
    )

    # 3. Сканируем боевую базу data/memory.db на отсутствие тестовых сессий
    if prod_memory_db.exists():
        conn = sqlite3.connect(str(prod_memory_db), timeout=5.0)
        try:
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_sessions'"
            ).fetchone()
            if tbl:
                rows = conn.execute("SELECT id, title FROM chat_sessions").fetchall()
                leaked_db_rows = [
                    f"{r[0]} ({r[1]})"
                    for r in rows
                    if str(r[0]).startswith("test_")
                    or any(m in str(r[1]).lower() for m in suspicious_markers)
                ]
                assert not leaked_db_rows, (
                    f"ОБНАРУЖЕНА УТЕЧКА ОТ ДРУГИХ ТЕСТОВ в боевую SQLite базу data/memory.db: {leaked_db_rows}"
                )
        finally:
            conn.close()
