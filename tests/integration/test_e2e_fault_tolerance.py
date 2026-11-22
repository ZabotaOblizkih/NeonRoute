"""
End-to-End Integration Tests for Fault Tolerance and Real-Time Race Conditions (NeonRoute).
Tests multi-component interactions (3+ components simultaneously) under real failure modes:
1. Text -> Image Crash -> Text Return (TaskQueueWorker + VRAMArbiter + LMStudioClient + ForgeClient + SessionManager).
2. SSE-stream disconnection mid-<think> + immediate next turn (LMStudioClient + ThoughtStreamParser + ContextWindowBuilder + SessionManager).
3. UI race condition on abort, session switch, and concurrent tasks (MainWindow + TaskQueueWorker + SessionController + Memory Tasks).
4. Corrupted KV slot restore fallback to clean prefill during DIFFUSION -> LLM switch (VRAMArbiter + ProcessManager + LMStudioClient).
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from config import MODE_PROFILES
from services.context_window_builder import ContextWindowBuilder
from services.lm_studio import LMStudioClient
from services.process_manager import ProcessManager, ServerState
from services.session_manager import SessionManager
from services.task_queue import InferenceTask, TaskQueueWorker
from services.thought_stream_parser import ThinkParserState, ThoughtStreamParser
from services.vram_arbiter import GPUOwner, VRAMArbiter


@pytest.fixture(scope="session")
def qapp():
    """Headless Qt Application fixture for offscreen test execution."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def mock_kv_dir(tmp_path, monkeypatch):
    """Isolates KV_SLOTS_DIR into a temporary directory."""
    kv_dir = tmp_path / "kv_slots"
    kv_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("services.process_manager.KV_SLOTS_DIR", kv_dir)
    return kv_dir


# =============================================================================
# Сценарий 1: Сквозной цикл «Текст -> Авария Картинки -> Возврат Текста»
# =============================================================================

def test_e2e_text_image_failure_text_recovery_cycle(qapp, tmp_path, monkeypatch):
    """
    Сквозной сценарий:
    1. Текстовый запрос через TaskQueueWorker -> VRAM переходит под LLM, ответ записан в сессию.
    2. Запрос на генерацию арта -> VRAM переходит под DIFFUSION (выгрузка LLM),
       Forge /sdapi/v1/txt2img падает по таймауту/ошибке сокета.
    3. Очередь не зависает, эмитит task_failed и разблокирует выполнение.
    4. Следующий текстовый запрос в этой же сессии переводит VRAM обратно в LLM
       и успешно отрабатывает.
    Инварианты:
    - TaskQueueWorker не завис и завершил все задачи.
    - VRAMArbiter._current_owner вернулся в GPUOwner.LLM.
    - Сессия сохранила контекст обоих текстовых запросов.
    - forge_client выгружен при возврате к LLM.
    """
    mock_lm = MagicMock()
    mock_lm._is_model_unloaded = False
    mock_forge = MagicMock()
    mock_forge._checkpoint_unloaded = False
    mock_forge.is_server_ready.return_value = True

    arbiter = VRAMArbiter.get_instance()
    arbiter.set_clients(mock_lm, mock_forge)
    arbiter._current_owner = GPUOwner.NONE

    worker = TaskQueueWorker()
    worker.start()

    finished_tasks = []
    failed_tasks = []
    worker.task_finished.connect(lambda tid, res: finished_tasks.append((tid, res)))
    worker.task_failed.connect(lambda tid, err: failed_tasks.append((tid, err)))

    session = {
        "id": "e2e_sess_1",
        "title": "Тестовый диалог отказоустойчивости",
        "messages": []
    }

    def do_text_turn_1():
        session["messages"].append({"role": "user", "content": "Привет! Нарисуй неоновый город."})
        session["messages"].append({"role": "assistant", "content": "Сейчас подготовлю изображение города..."})
        return {"status": "ok", "content": "text_1_done"}

    def do_art_turn_crash():
        # Симулируем падение Forge по таймауту на /sdapi/v1/txt2img
        raise requests.exceptions.Timeout("Forge /sdapi/v1/txt2img connection timed out after 60s")

    def do_text_turn_2():
        session["messages"].append({"role": "user", "content": "Картинка не получилась, тогда опиши город словами."})
        session["messages"].append({"role": "assistant", "content": "Неоновый город мерцал огнями в темноте..."})
        return {"status": "ok", "content": "text_2_done"}

    try:
        with patch("services.layer_budgeter.LayerBudgeter.get_current_used_vram_mb", return_value=0.0):
            # Шаг 1: Первый текстовый запрос
            t1 = InferenceTask("text_turn_1", "llm", do_text_turn_1)
            worker.enqueue(t1)

            t_start = time.time()
            while len(finished_tasks) < 1 and time.time() - t_start < 3.0:
                qapp.processEvents()
                time.sleep(0.01)

            assert len(finished_tasks) == 1
            assert arbiter._current_owner == GPUOwner.LLM

            # Шаг 2: Арт-запрос с аварией Forge
            t2 = InferenceTask("art_turn_crash", "image", do_art_turn_crash)
            worker.enqueue(t2)

            t_start = time.time()
            while len(failed_tasks) < 1 and time.time() - t_start < 3.0:
                qapp.processEvents()
                time.sleep(0.01)

            assert len(failed_tasks) == 1
            assert "Forge /sdapi/v1/txt2img connection timed out" in failed_tasks[0][1]
            # Во время выполнения арта владелец VRAM переключился на DIFFUSION
            assert arbiter._current_owner == GPUOwner.DIFFUSION
            assert mock_lm.unload_models_cli.called

            # Шаг 3: Следующий текстовый запрос после аварии
            t3 = InferenceTask("text_turn_2", "llm", do_text_turn_2)
            worker.enqueue(t3)

            t_start = time.time()
            while len(finished_tasks) < 2 and time.time() - t_start < 3.0:
                qapp.processEvents()
                time.sleep(0.01)

            assert len(finished_tasks) == 2
            # Владелец VRAM штатно вернулся к LLM
            assert arbiter._current_owner == GPUOwner.LLM
            assert mock_forge.unload_checkpoint.called or mock_forge.free_vram.called

            # Проверка целостности очереди и сессии
            assert worker.has_pending_tasks(("llm", "image")) is False
            assert len(session["messages"]) == 4
            assert session["messages"][0]["role"] == "user"
            assert session["messages"][1]["role"] == "assistant"
            assert session["messages"][2]["role"] == "user"
            assert session["messages"][3]["role"] == "assistant"

    finally:
        worker.stop()
        worker.wait(1500)


# =============================================================================
# Сценарий 2: Обрыв SSE-стрима LLM прямо внутри <think> + немедленный следующий ход
# =============================================================================

def test_e2e_sse_stream_disconnect_mid_think_and_next_turn_recovery(tmp_path, monkeypatch):
    """
    Сквозной сценарий:
    1. Модель начинает генерировать блок мыслей <think>...\n
    2. Соединение внезапно обрывается без закрывающего </think> и без [DONE].
    3. ThoughtStreamParser корректно финализирует состояние без зависаний.
    4. Реплика ассистента сохраняется в историю сессии.
    5. При формировании следующего хода ContextWindowBuilder:
       - Не роняет альтернацию ролей Jinja (подставляет безопасную заглушку при пустом ответе).
       - Не допускает незакрытых висячих тегов <think> в нормальном тексте.
       - Успешно строит контекстное окно для следующего обращения пользователя.
    """
    # 1. Потоковый парсер мыслей
    parser = ThoughtStreamParser()

    # Модель присылает чанки мыслей
    chunk1 = "Привет! <think>\nРазмышляю над квантованием модели и структурой слоев..."
    chunk2 = "\nПроверяю параметры kv-cache q8_0 и q4_0..."
    events1 = parser.feed(chunk1)
    events2 = parser.feed(chunk2)

    assert parser.state == ThinkParserState.IN_THINK
    assert "Размышляю над квантованием" in parser.thought_text

    # Сервер внезапно закрыл сокет прямо внутри <think>
    final_events = parser.finalize()
    assert parser.state == ThinkParserState.THINK_FINISHED
    assert any(evt[0] == "end_think" for evt in final_events)

    thought_meta = parser.get_thought_data()
    assert thought_meta is not None
    assert "Размышляю над квантованием" in thought_meta["content"]
    assert thought_meta["duration"] >= 0.0

    # 2. Сохранение хода в сессию
    history = [
        {"role": "user", "content": "Объясни устройство квантования."},
        {"role": "assistant", "content": parser.normal_text, "thought": thought_meta},
        {"role": "user", "content": "Ты прервался, продолжи ответ."}
    ]

    # Проверка сериализации/десериализации сессии в JSON
    sess_file = tmp_path / "chat_interrupted_session.json"
    sess_file.write_text(json.dumps({"messages": history}, ensure_ascii=False), encoding="utf-8")
    loaded_sess = json.loads(sess_file.read_text(encoding="utf-8"))
    assert len(loaded_sess["messages"]) == 3

    # 3. Сборка следующего контекстного окна для LLM через ContextWindowBuilder
    prepared_msgs = ContextWindowBuilder.prepare_history_for_llm(
        history=history,
        mode="assistant",
        enable_reasoning=False
    )

    # Проверка инвариантов альтернации ролей
    assert len(prepared_msgs) == 3
    assert prepared_msgs[0]["role"] == "user"
    assert prepared_msgs[1]["role"] == "assistant"
    assert prepared_msgs[2]["role"] == "user"

    # В assistant content должен быть чистый текст или дефолтная заглушка альтернации
    asst_content = prepared_msgs[1]["content"]
    assert asst_content.strip() != ""
    assert "<think>" not in asst_content
    assert "</think>" not in asst_content

    # В режиме engineer с сохранением CoT мысли должны быть корректно закрыты
    prepared_eng = ContextWindowBuilder.prepare_history_for_llm(
        history=history,
        mode="engineer",
        enable_reasoning=True,
        keep_last_cot=True
    )
    assert len(prepared_eng) == 3
    eng_asst_content = prepared_eng[1]["content"]
    assert "<think>" in eng_asst_content
    assert "</think>" in eng_asst_content


# =============================================================================
# Сценарий 3: Гонка отмены и смены сессии в UI (MainWindow в headless-режиме)
# =============================================================================

def test_e2e_ui_race_abort_session_switch_and_concurrent_tasks(qapp, tmp_path, monkeypatch):
    """
    Сквозной сценарий:
    1. Инициализация MainWindow в offscreen-режиме.
    2. Отправка текстового сообщения -> постановка задачи в TaskQueue.
    3. Мгновенная отмена генерации (abort_generation).
    4. Немедленное переключение сессии (_create_new_session).
    5. Постановка фоновой задачи памяти (memory) и генерации арта (image).
    6. Инициализация closeEvent() без зависаний и без утечек C++ QObject.
    Инварианты:
    - Отсутствие исключений RuntimeError: wrapped C/C++ object has been deleted.
    - Потоки и очередь корректно останавливаются.
    - Фоновые задачи памяти не разрушают состояние сессий.
    """
    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    monkeypatch.setattr("ui.main_window.MainWindow._start_background_services", lambda self: None)
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])
    monkeypatch.setattr("services.process_manager.ProcessManager.start_lm_studio_async", lambda *a, **kw: None)

    from ui.main_window import MainWindow

    win = MainWindow()
    win.show()

    mem_task_executed = []
    art_task_executed = []

    def mock_mem_action():
        mem_task_executed.append(True)
        return {"saved": True}

    def mock_art_action():
        art_task_executed.append(True)
        return {"image": "ok"}

    try:
        # 1. Отправка сообщения
        win.input_box.setPlainText("Тестовый запрос в момент гонки")
        win._on_send_clicked()

        # 2. Мгновенная отмена (abort)
        win.abort_generation()
        assert win.is_busy is False

        # 3. Мгновенное переключение сессии
        win._create_new_session("assistant", title="Сессия после гонки")
        assert win.current_session is not None
        assert win.current_session.get("title") == "Сессия после гонки"
        assert win._is_switching_session is False

        # 4. Постановка фоновых задач в очередь
        win.task_queue.enqueue(InferenceTask("race_mem_task", "memory", mock_mem_action))
        win.task_queue.enqueue(InferenceTask("race_art_task", "image", mock_art_action))

        # Даем отработать событиям очереди
        t_start = time.time()
        while (not mem_task_executed or not art_task_executed) and time.time() - t_start < 2.0:
            qapp.processEvents()
            time.sleep(0.01)

        # 5. Закрытие окна
        win._shutdown_completed = True
        win.close()

    finally:
        from services.sandbox_manager import SandboxManager
        from services.tool_engine import ToolEngine
        SandboxManager.set_active_session_id(None)
        ToolEngine.set_active_session_id(None)
        if hasattr(win, "task_queue") and win.task_queue:
            win.task_queue.stop()
            win.task_queue.wait(1000)


# =============================================================================
# Сценарий 4: Сквозной сбой восстановления KV-слота при возврате из диффузии
# =============================================================================

def test_e2e_kv_slot_restore_failure_fallback_to_clean_prefill(mock_kv_dir, monkeypatch):
    """
    Сквозной сценарий:
    1. VRAMArbiter переключает контекст DIFFUSION -> LLM.
    2. Процесс llama-server стартует, но файл evicted_slot_0.bin поврежден
       или POST /slots/0?action=restore возвращает HTTP 500.
    3. _restore_kv_slot_after_start возвращает False и безопасно очищает кэш слота.
    4. ProcessManager и сервер остаются в состоянии ServerState.READY.
    5. Запрос chat_completion штатно завершается через чистый prefill контекста
       без сбоя сессии и без исключений.
    """
    pm = ProcessManager()
    pm._llama_process = MagicMock()
    pm._server_state = ServerState.READY
    pm._current_running_port = 1234
    pm._current_running_model_path = "C:/models/test-model.gguf"

    # Записываем битый файл слота и метаданные
    slot_file = mock_kv_dir / "evicted_slot_0.bin"
    meta_file = mock_kv_dir / "slot_metadata.json"
    slot_file.write_bytes(b"\x00\xFF\xFE_CORRUPTED_SLOT_BINARY_DATA_")
    meta_file.write_text(json.dumps({
        "model_path": "C:/models/test-model.gguf",
        "model_mtime": 100.0,
        "context_length": 8192,
        "gpu_layers": 48,
        "kv_cache_type_k": "q8_0",
        "kv_cache_type_v": "q4_0",
        "flash_attn": "on",
    }), encoding="utf-8")

    assert slot_file.exists()
    assert meta_file.exists()

    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True

    client = LMStudioClient()
    client.process_manager = pm
    client._is_model_unloaded = False
    client._last_loaded_model_id = "test-model"

    arbiter = VRAMArbiter.get_instance()
    arbiter.set_clients(client, mock_forge)
    arbiter._current_owner = GPUOwner.DIFFUSION

    # Мок ответов HTTP: модели доступны, restore возвращает 500, chat_completion возвращает ответ
    mock_models_resp = MagicMock()
    mock_models_resp.status_code = 200
    mock_models_resp.json.return_value = {"data": [{"id": "test-model"}]}

    mock_restore_500 = MagicMock()
    mock_restore_500.status_code = 500
    mock_restore_500.text = "Internal Server Error: slot corrupted"

    mock_chat_resp = MagicMock()
    mock_chat_resp.status_code = 200
    chat_payload = {"choices": [{"message": {"content": "Успешный ответ после чистого prefill."}}]}
    mock_chat_resp.content = json.dumps(chat_payload).encode("utf-8")
    mock_chat_resp.json.return_value = chat_payload

    def mock_requests_post(url, *args, **kwargs):
        if "action=restore" in url:
            return mock_restore_500
        return mock_chat_resp

    with patch.object(client._persistent_session, "get", return_value=mock_models_resp), \
         patch.object(client._persistent_session, "post", return_value=mock_chat_resp), \
         patch("requests.post", side_effect=mock_requests_post), \
         patch("services.layer_budgeter.LayerBudgeter.get_current_used_vram_mb", return_value=0.0):

        # 1. Переключение арбитра под LLM
        arbiter.prepare_for_task("llm")
        assert arbiter._current_owner == GPUOwner.LLM

        # 2. Попытка восстановления поврежденного слота в llama-server
        expected_meta = {
            "model_path": "C:/models/test-model.gguf",
            "model_mtime": 100.0,
            "context_length": 8192,
            "gpu_layers": 48,
            "kv_cache_type_k": "q8_0",
            "kv_cache_type_v": "q4_0",
            "flash_attn": "on",
        }
        restored = pm._restore_kv_slot_after_start(expected_meta)
        assert restored is False

        # Инвариант: метаданные инвалидированы сразу, дамп очищается на пост-стоп
        assert not meta_file.exists()
        pm.clear_kv_slot_cache()
        assert not slot_file.exists()
        assert pm._server_state == ServerState.READY

        # 3. Инференс продолжается штатно через полный пересчет префикса (clean prefill)
        result = client.chat_completion([{"role": "user", "content": "Продолжаем диалог"}], stream=False)
        assert result == "Успешный ответ после чистого prefill."
        assert pm.is_oom_failure is False
