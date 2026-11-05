"""
Characterization Baseline Tests for MainWindow (NeonRoute).
Freezes the current contract and integration boundaries of MainWindow
before decomposition and modularization.

Strictly non-intrusive: mocks all external network and daemon services.
Runs in headless offscreen mode in < 5 seconds.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QCloseEvent
from services.mode_coordinator import AppMode
from services.sandbox_manager import SandboxManager
from services.tool_engine import ToolEngine
from ui.controllers.reminder_controller import ReminderController


@pytest.fixture(scope="session")
def qapp():
    """Headless Qt Application fixture for offscreen test execution."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def main_window(qapp, monkeypatch):
    """
    Isolated MainWindow fixture with suppressed background socket/threads.
    Ensures safe lifecycle and zero state leak across test runs.
    """
    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    monkeypatch.setattr("ui.main_window.MainWindow._start_background_services", lambda self: None)
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])
    monkeypatch.setattr("services.process_manager.ProcessManager.start_lm_studio_async", lambda *a, **kw: None)

    from ui.main_window import MainWindow

    win = MainWindow()
    win.show()

    yield win

    # Safe teardown: prevent ShutdownWorker timeout and clean up active session
    win._shutdown_completed = True
    win.close()
    SandboxManager.set_active_session_id(None)
    ToolEngine.set_active_session_id(None)


# =============================================================================
# 1. Жизненный цикл и инициализация окна (Smoke)
# =============================================================================

def test_mainwindow_initialization_smoke(main_window):
    """Проверяет успешное создание MainWindow и привязку всех ключевых контроллеров."""
    assert main_window is not None

    # Контроллеры и сервисы
    assert hasattr(main_window, "reminder_controller")
    assert isinstance(main_window.reminder_controller, ReminderController)
    assert hasattr(main_window, "mode_coordinator")
    assert hasattr(main_window, "task_queue")
    assert hasattr(main_window, "lm_client")
    assert hasattr(main_window, "forge_client")
    assert hasattr(main_window, "art_director")

    # UI-компоненты
    assert hasattr(main_window, "input_box")
    assert hasattr(main_window, "sidebar")
    assert hasattr(main_window, "workspace_inspector")
    assert hasattr(main_window, "chat_stream")

    # Методы миксинов потока чата и инженера
    assert callable(getattr(main_window, "_on_send_clicked", None))
    assert callable(getattr(main_window, "_on_llm_response_received", None))
    assert callable(getattr(main_window, "_force_reset_busy_state", None))
    assert callable(getattr(main_window, "is_engineer_mode_active", None))
    assert callable(getattr(main_window, "_activate_engineering_mode", None))
    assert callable(getattr(main_window, "_deactivate_engineering_mode", None))


# =============================================================================
# 2. Контракт переключения режимов (Engineer vs Assistant/Roleplay)
# =============================================================================

def test_mode_switching_contract_engineer_vs_assistant(main_window):
    """
    Проверяет переход между режимами:
    активацию флагов, синхронизацию сплиттера/инспектора и запись в current_session.
    """
    # 1. Исходное состояние (по умолчанию Assistant)
    assert main_window.is_engineer_mode_active() is False
    assert main_window.workspace_inspector.isVisible() is False

    # 2. Переключение в инженерный режим через ModeCoordinator
    ok_eng = main_window.mode_coordinator.transition_to(AppMode.ENGINEER)
    assert ok_eng is True
    assert main_window.is_engineer_mode_active() is True
    assert main_window.workspace_inspector.isVisible() is True
    assert main_window.current_session is not None
    assert main_window.current_session.get("type") == "engineering"

    # 3. Проверка прямого вызова _activate_engineering_mode миксина
    main_window._activate_engineering_mode()
    assert main_window._engineering_mode_active is True
    assert main_window.current_session.get("type") == "engineering"

    # 4. Обратное переключение в режим ассистента
    ok_asst = main_window.mode_coordinator.transition_to(AppMode.ASSISTANT)
    assert ok_asst is True
    assert main_window.is_engineer_mode_active() is False
    assert main_window.workspace_inspector.isVisible() is False


# =============================================================================
# 3. Событийная цепочка отправки сообщения (_on_send_clicked)
# =============================================================================

def test_message_sending_event_chain(main_window):
    """
    Проверяет цепочку отправки:
    очистку инпута, прерывание фоновых задач памяти и постановку задачи в TaskQueue.
    """
    main_window.task_queue.abort_tasks_by_type = MagicMock()
    main_window.task_queue.enqueue = MagicMock()

    test_prompt = "Тестовый вопрос для проверки цепочки отправки"
    main_window.input_box.setPlainText(test_prompt)
    assert main_window.input_box.toPlainText().strip() == test_prompt

    main_window._on_send_clicked()

    # Поле ввода очищено
    assert main_window.input_box.toPlainText().strip() == ""

    # Вытеснение фонового анализа сцены вызвано с типом 'memory'
    main_window.task_queue.abort_tasks_by_type.assert_called_once_with("memory")

    # В очередь задач поставлена задача инференса LLM
    assert main_window.task_queue.enqueue.called
    enqueued_task = main_window.task_queue.enqueue.call_args[0][0]
    assert enqueued_task.task_type == "llm"


# =============================================================================
# 4. Маршрутизация ответов и ошибок
# =============================================================================

def test_llm_response_routing_and_busy_state(main_window):
    """Проверяет корректное завершение генерации и сброс состояния занятости."""
    main_window.is_busy = True
    main_window.input_box.set_busy(True)

    # Вызов слота ответа не падает и финализирует генерацию
    sample_response = "Привет! Я твой локальный ИИ ассистент."
    main_window._on_llm_response_received(sample_response)

    assert main_window.is_busy is False


def test_llm_error_routing_and_recovery(main_window):
    """Проверяет реакцию на сбой инференса и разблокировку ввода."""
    main_window.is_busy = True
    main_window.input_box.set_busy(True)

    # 1. Сигнал ошибки от клиента LM Studio обрабатывается безопасно
    main_window._on_llm_error_occurred("Network connection reset by peer")

    # 2. Обработка провала задачи сбрасывает флаги занятости
    main_window._on_task_failed("test_task_id", "Inference timeout")
    assert main_window.is_busy is False

    # 3. Принудительный сброс разблокирует поле ввода
    main_window.is_busy = True
    main_window._force_reset_busy_state()
    assert main_window.is_busy is False
    if hasattr(main_window.input_box, "text_input") and main_window.input_box.text_input:
        assert main_window.input_box.text_input.isReadOnly() is False


# =============================================================================
# 5. Изоляция и контракты ReminderController
# =============================================================================

def test_reminder_controller_isolation_and_trigger(main_window):
    """
    Проверяет реакцию окна на напоминание:
    добавление баббла ассистента в ленту и сохранение в сессию.
    """
    initial_count = len(main_window.chat_history)
    reminder_text = "Проверить компиляцию тестов"

    # Прямой вызов обработчика напоминания
    main_window._on_reminder_triggered(reminder_text)

    assert len(main_window.chat_history) > initial_count
    last_msg = main_window.chat_history[-1]
    assert last_msg["role"] == "assistant"
    assert reminder_text in last_msg["content"]
    assert "Напоминаю:" in last_msg["content"]

    # Проверка срабатывания через Qt-сигнал контроллера
    signal_reminder_text = "Системное напоминание из таймера"
    main_window.reminder_controller.reminder_triggered.emit(signal_reminder_text)

    signal_msg = main_window.chat_history[-1]
    assert signal_msg["role"] == "assistant"
    assert signal_reminder_text in signal_msg["content"]


# =============================================================================
# 6. Корректность закрытия окна (closeEvent)
# =============================================================================

def test_close_event_graceful_shutdown(main_window):
    """
    Проверяет graceful shutdown:
    остановку таймера напоминаний, скрытие трея, отмену warmup арт-директора
    и перевод флага _shutdown_in_progress.
    """
    # Мокаем методы завершения для предотвращения выхода из pytest
    mock_cancel_warmup = MagicMock()
    if hasattr(main_window, "art_director") and hasattr(main_window.art_director, "router"):
        main_window.art_director.router.cancel_deferred_warmup = mock_cancel_warmup

    mock_timer_stop = MagicMock()
    mock_tray_hide = MagicMock()
    main_window.reminder_controller._timer.stop = mock_timer_stop
    main_window.reminder_controller.tray_icon.hide = mock_tray_hide

    with patch("ui.workers.shutdown_worker.ShutdownWorker.start") as mock_sw_start, \
         patch("PyQt6.QtWidgets.QApplication.quit") as mock_quit:
        event = QCloseEvent()
        main_window.closeEvent(event)

        # Проверка остановки фоновых сервисов
        assert mock_cancel_warmup.called
        assert mock_timer_stop.called
        assert mock_tray_hide.called
        assert main_window._shutdown_in_progress is True
        assert mock_sw_start.called
