"""
Consolidated End-to-End Smoke Test Suite for NeonRoute Core Lifecycle, UI Coordination & SQLite Storage.
Covers:
1. Headless MainWindow lifecycle & 3-mode transitions via ModeCoordinator (Assistant, Roleplay, Engineer).
2. Startup Non-Blocking Guard: intercepting blocking network calls in MainThread.
3. Three-mode SQLite WAL session persistence & fidelity (Assistant, Roleplay, Engineer).
4. External JSON edit synchronization into SQLite WAL (BFF mobile_server sync).
5. Cross-Session Isolation & Production Leak Guard (verifying zero leaks in real history/ and data/memory.db).
6. ACID Atomic JSON persistence (atomic_write_json).
7. UIContext SSOT headless turn lifecycle.
8. HybridSearchEngine in-memory indexing & search.
9. TokenBudgetManager emergency VRAM guard.
"""

import os
import json
import sqlite3
import threading
import traceback
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from PyQt6.QtWidgets import QApplication, QWidget

import config
import services.session_manager as sm_mod
from services.session_manager import SessionManager
from services.mode_coordinator import ModeCoordinator, AppMode
from services.sandbox_manager import SandboxManager
from services.memory_db import memory_db
from services.hybrid_search import HybridSearchEngine
from services.token_budget_manager import TokenBudgetManager
from utils.file_utils import atomic_write_json
from ui.common.ui_context import UIContext


# =========================================================================
# СЦЕНАРИЙ 1: ЖИЗНЕННЫЙ ЦИКЛ MAINWINDOW И БЕЗОПАСНОСТЬ ГЛАВНОГО ПОТОКА
# =========================================================================

def test_mainwindow_lifecycle_and_non_blocking_guard(smoke_isolation_guard, qapp, monkeypatch):
    """
    Сквозной смоук-тест:
    1. Инициализация MainWindow в headless-режиме, инварианты полей состояния и переключение 3 режимов через ModeCoordinator.
    2. Guard отсутствия блокирующих сетевых запросов (requests.get/post) в MainThread во время старта.
    """
    scen = "Сценарий 1: Жизненный цикл MainWindow и Non-Blocking Guard"

    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    monkeypatch.setattr("services.process_manager.ProcessManager.is_lm_studio_running", lambda self: True)
    monkeypatch.setattr("services.process_manager.ProcessManager.start_lm_studio_async", lambda *a, **kw: None)
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])

    # Перехват сетевых вызовов в MainThread для защиты от GUI-фризов
    import requests
    violations = []
    orig_get = requests.get
    orig_post = requests.post

    def guard_get(*args, **kwargs):
        if threading.current_thread().name == "MainThread":
            violations.append({"type": "HTTP_GET", "target": args[0] if args else "", "caller": traceback.format_stack()[-2].strip()})
        return orig_get(*args, **kwargs)

    def guard_post(*args, **kwargs):
        if threading.current_thread().name == "MainThread":
            violations.append({"type": "HTTP_POST", "target": args[0] if args else "", "caller": traceback.format_stack()[-2].strip()})
        return orig_post(*args, **kwargs)

    monkeypatch.setattr(requests, "get", guard_get)
    monkeypatch.setattr(requests, "post", guard_post)

    from ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_start_background_services", lambda self: None)

    window = MainWindow()
    window.show()

    try:
        # 1. Проверяем базовые поля состояния
        assert_diag(window is not None, scen, "MainWindow instance", "Initialized", window is not None)
        assert_diag(hasattr(window, "current_session"), scen, "Поле current_session", True, hasattr(window, "current_session"))
        assert_diag(hasattr(window, "mode_coordinator"), scen, "Поле mode_coordinator", True, hasattr(window, "mode_coordinator"))

        # 2. Переключение FSM в Инженер и Ассистент
        ok_eng = window.mode_coordinator.transition_to(AppMode.ENGINEER)
        assert_diag(ok_eng is True, scen, "Переход в ENGINEER", True, ok_eng)
        assert_diag(window.mode_coordinator.current_mode == AppMode.ENGINEER, scen, "Текущий режим", AppMode.ENGINEER, window.mode_coordinator.current_mode)

        ok_asst = window.mode_coordinator.transition_to(AppMode.ASSISTANT)
        assert_diag(ok_asst is True, scen, "Переход в ASSISTANT", True, ok_asst)
        assert_diag(window.mode_coordinator.current_mode == AppMode.ASSISTANT, scen, "Текущий режим", AppMode.ASSISTANT, window.mode_coordinator.current_mode)

        # 3. Проверка отсутствия сетевых блокировок GUI-потока
        assert_diag(len(violations) == 0, scen, "Блокирующие сетевые вызовы в MainThread", 0, len(violations))
    finally:
        window._shutdown_completed = True
        window.close()
        window.deleteLater()
        SandboxManager.set_active_session_id(None)


# =========================================================================
# СЦЕНАРИЙ 2: СКВОЗНАЯ ПЕРСИСТЕНТНОСТЬ 3 РЕЖИМОВ В SQLITE WAL
# =========================================================================

def test_three_modes_sqlite_wal_recording_and_fidelity(smoke_isolation_guard):
    """
    Проверяет, что чаты всех 3 режимов (Assistant, Roleplay, Engineer) записываются
    в SQLite WAL (таблицы chat_sessions и chat_messages) + быстрое зеркало,
    сохраняя все специфичные для каждого режима поля без потерь.
    """
    scen = "Сценарий 2: Персистентность 3 режимов в SQLite WAL"

    # 1. Чат режима АССИСТЕНТ (с чипами RAG и блоком рассуждений <think>)
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
        assert_diag(saved_path is not None and saved_path.exists(), scen, f"Save session {s['mode']}", True, saved_path.exists() if saved_path else False)

    # Проверка SQLite WAL напрямую
    with SessionManager._get_sqlite_conn() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert_diag(str(journal_mode).lower() == "wal", scen, "SQLite journal_mode", "wal", str(journal_mode).lower())

        for s in three_sessions:
            sid = s["id"]
            row = conn.execute("SELECT id, title, mode, header_json FROM chat_sessions WHERE id = ?", (sid,)).fetchone()
            assert_diag(row is not None, scen, f"Запись в chat_sessions для {s['mode']}", "Строка существует", row is not None)
            assert_diag(row["title"] == s["title"], scen, f"Заголовок сессии {s['mode']}", s["title"], row["title"])
            assert_diag(row["mode"] == s["mode"], scen, f"Режим сессии {s['mode']}", s["mode"], row["mode"])

            msg_rows = conn.execute(
                "SELECT message_idx, msg_id, role, text, raw_msg_json FROM chat_messages WHERE session_id = ? ORDER BY message_idx ASC",
                (sid,),
            ).fetchall()
            assert_diag(len(msg_rows) == 2, scen, f"Число сообщений в SQLite для {s['mode']}", 2, len(msg_rows))

    # Очистка оперативного кэша и чтение из SQLite
    SessionManager._sessions_cache.clear()
    SessionManager._current_session = None

    loaded_ast = SessionManager.load_session(sess_assistant["id"])
    assert_diag(loaded_ast["metadata"]["active_assistant_skill"] == "analyst_logic", scen, "Loaded Assistant Skill", "analyst_logic", loaded_ast["metadata"]["active_assistant_skill"])
    assert_diag(loaded_ast["messages"][1]["thought"] == "Сверяю реестр документации RAG...", scen, "Loaded Assistant Thought", "Сверяю реестр...", loaded_ast["messages"][1]["thought"])

    loaded_rp = SessionManager.load_session(sess_roleplay["id"])
    assert_diag(loaded_rp["character_data"]["name"] == "Веспера", scen, "Loaded Roleplay Character", "Веспера", loaded_rp["character_data"]["name"])
    assert_diag(loaded_rp["messages"][1]["visual_intent"]["scene_type"] == "exploration", scen, "Loaded Visual Intent", "exploration", loaded_rp["messages"][1]["visual_intent"]["scene_type"])

    loaded_eng = SessionManager.load_session(sess_engineer["id"])
    assert_diag(loaded_eng["type"] == "engineering", scen, "Loaded Engineer Type", "engineering", loaded_eng["type"])
    assert_diag(loaded_eng["messages"][1]["generation_params"]["passed_tdd"] is True, scen, "Loaded TDD flag", True, loaded_eng["messages"][1]["generation_params"]["passed_tdd"])


# =========================================================================
# СЦЕНАРИЙ 3: ВНЕШНЯЯ СИНХРОНИЗАЦИЯ JSON -> SQLITE (MOBILE BFF)
# =========================================================================

def test_external_json_sync_into_sqlite_wal(smoke_isolation_guard):
    """
    Проверяет, что если внешний процесс (mobile_server) обновил файл chat_<id>.json,
    SessionManager.load_session обнаруживает новый st_mtime и синхронизирует изменения в SQLite.
    """
    scen = "Сценарий 3: Синхронизация внешних правок JSON -> SQLite"

    sess = SessionManager.create_session(mode="assistant", title="Проверка Внешней Синхронизации")
    sid = sess["id"]
    json_path = SessionManager.save_session(sess)
    assert_diag(bool(json_path and json_path.exists()), scen, "Первичное сохранение", True, json_path.exists() if json_path else False)

    # Имитируем внешнюю правку JSON с новой репликой и сдвигом mtime на 5 секунд вперед
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
    assert_diag(len(reloaded["messages"]) == 1, scen, "Сообщений после внешней правки", 1, len(reloaded["messages"]))
    assert_diag(reloaded["messages"][0]["id"] == "msg_ext_mobile", scen, "ID внешней реплики", "msg_ext_mobile", reloaded["messages"][0]["id"])

    # Проверка фиксации в SQLite
    with SessionManager._get_sqlite_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (sid,)).fetchone()[0]
        assert_diag(cnt == 1, scen, "Синхронизация в SQLite", 1, cnt)


# =========================================================================
# СЦЕНАРИЙ 4: ПРОВЕРКА ОТСУТСТВИЯ УТЕЧЕК В БОЕВОЕ ОКРУЖЕНИЕ (LEAK GUARD)
# =========================================================================

def test_production_leak_guard_and_isolation(smoke_isolation_guard):
    """
    Сторожевой тест: сканирует реальные боевые каталоги C:/NeonRoute/history
    и C:/NeonRoute/data/memory.db, гарантируя полное отсутствие тестовых артефактов.
    """
    scen = "Сценарий 4: Production Leak Guard"

    project_root = Path(__file__).resolve().parent.parent.parent
    prod_history_dir = project_root / "history"
    prod_memory_db = project_root / "data" / "memory.db"

    # 1. Проверка изоляции активных путей
    assert_diag(str(smoke_isolation_guard["history_dir"]) in str(sm_mod.HISTORY_DIR), scen, "Изоляция SessionManager.HISTORY_DIR", "Временный каталог", sm_mod.HISTORY_DIR)
    assert_diag(str(smoke_isolation_guard["data_dir"]) in str(memory_db.db_path), scen, "Изоляция memory_db.db_path", "Временный каталог", memory_db.db_path)

    # 2. Сканирование боевой папки history
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

    assert_diag(len(leaked_json_files) == 0, scen, "Утечки в боевую history/", "0 утечек", leaked_json_files)

    # 3. Сканирование боевой базы memory.db
    if prod_memory_db.exists():
        conn = sqlite3.connect(str(prod_memory_db), timeout=5.0)
        try:
            tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_sessions'").fetchone()
            if tbl:
                rows = conn.execute("SELECT id, title FROM chat_sessions").fetchall()
                leaked_db_rows = [
                    f"{r[0]} ({r[1]})"
                    for r in rows
                    if str(r[0]).startswith("test_") or any(m in str(r[1]).lower() for m in suspicious_markers)
                ]
                assert_diag(len(leaked_db_rows) == 0, scen, "Утечки в боевую SQLite data/memory.db", "0 утечек", leaked_db_rows)
        finally:
            conn.close()


# =========================================================================
# СЦЕНАРИЙ 5: СЕРВИСНЫЕ ИНВАРИАНТЫ ЯДРА (ATOMIC WRITE, UICONTEXT, SEARCH, BUDGET)
# =========================================================================

def test_core_subsystems_atomic_search_and_budget(smoke_isolation_guard, qapp):
    """
    Проверка фундаментальных сервисов ядра:
    - atomic_write_json (атомарность через rename);
    - UIContext SSOT (begin_turn / finish_turn);
    - HybridSearchEngine (в памяти без ONNX);
    - TokenBudgetManager аварийный порог VRAM.
    """
    scen = "Сценарий 5: Сервисные инварианты ядра"

    # 1. atomic_write_json
    test_json = smoke_isolation_guard["data_dir"] / "acid_smoke.json"
    ok_atomic = atomic_write_json(test_json, {"isolated": True})
    assert_diag(ok_atomic is True, scen, "atomic_write_json статус", True, ok_atomic)
    assert_diag(test_json.exists(), scen, "atomic_write_json существование файла", True, test_json.exists())

    # 2. UIContext SSOT
    ui_ctx = UIContext()
    dummy_widget = QWidget()
    ui_ctx.begin_turn(dummy_widget)
    assert_diag(ui_ctx.is_busy is True and ui_ctx.turn_bubble is dummy_widget, scen, "UIContext begin_turn", True, ui_ctx.is_busy)
    ui_ctx.finish_turn()
    assert_diag(ui_ctx.is_busy is False and ui_ctx.turn_bubble is None, scen, "UIContext finish_turn", False, ui_ctx.is_busy)
    dummy_widget.deleteLater()

    # 3. HybridSearchEngine in-memory
    rag_engine = HybridSearchEngine(dense_vectorizer=False)
    chunks = rag_engine.chunk_text(
        "Артикул детали NR-9090 отвечает за охлаждение квантового ядра NeonRoute.",
        filename="manual_spec.md"
    )
    rag_engine.index_chunks(chunks)
    hits = rag_engine.search("Артикул NR-9090 охлаждение", top_k=1)
    assert_diag(len(hits) >= 1 and "NR-9090" in hits[0].get("text", str(hits[0])), scen, "HybridSearch hits", "Найден чанк с NR-9090", hits[0].get("text") if hits else None)

    # 4. TokenBudgetManager: аварийный порог VRAM
    tier_normal = TokenBudgetManager.calculate_context_budget(2000, mode="assistant", free_vram_mb=2000)
    tier_emergency = TokenBudgetManager.calculate_context_budget(2000, mode="assistant", free_vram_mb=100)
    assert_diag(tier_emergency.max_tokens <= tier_normal.max_tokens, scen, "Аварийный бюджет VRAM <= обычного", True, tier_emergency.max_tokens <= tier_normal.max_tokens)
    assert_diag(tier_emergency.max_tokens == 512, scen, "Аварийный лимит токенов", 512, tier_emergency.max_tokens)
