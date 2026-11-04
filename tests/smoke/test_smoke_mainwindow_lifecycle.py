import pytest
from PyQt6.QtWidgets import QApplication
from services.mode_coordinator import AppMode
from services.sandbox_manager import SandboxManager

@pytest.fixture(scope="session")
def qapp():
    """Headless Qt Application fixture."""
    app = QApplication.instance()
    if app is None:
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication(["-platform", "offscreen"])
    return app

def test_mainwindow_headless_lifecycle(qapp, monkeypatch):
    """Смок-тест инициализации MainWindow, контекста сессии и переключения режимов."""
    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    
    from ui.main_window import MainWindow

    # Подавляем сетевые фоновые сокеты для мгновенного offscreen старта
    monkeypatch.setattr(MainWindow, "_start_background_services", lambda self: None)

    window = MainWindow()
    window.show()

    try:
        # 1. Проверяем наличие ключевых полей состояния
        assert window is not None
        assert hasattr(window, "current_session")
        assert hasattr(window, "_turn_bubble")
        assert hasattr(window, "mode_coordinator")
        assert window.mode_coordinator.current_mode is not None

        # 2. Проверяем переключение режимов
        ok_eng = window.mode_coordinator.transition_to(AppMode.ENGINEER)
        assert ok_eng is True
        assert window.mode_coordinator.current_mode == AppMode.ENGINEER
        assert window.workspace_inspector.isVisible()

        ok_asst = window.mode_coordinator.transition_to(AppMode.ASSISTANT)
        assert ok_asst is True
        assert window.mode_coordinator.current_mode == AppMode.ASSISTANT
    finally:
        window._shutdown_completed = True
        window.close()
        # Сброс активной сессии для предотвращения утечки состояния
        SandboxManager.set_active_session_id(None)

def test_startup_non_blocking_guard(qapp, monkeypatch):
    """Сторожевой смок-тест: проверяет отсутствие блокирующих вызовов (сокеты, сеть, слипы)
    в главном GUI-потоке при реальном холодном старте MainWindow и фоновых сервисов.
    """
    import time
    import threading
    import traceback
    import requests
    import socket
    from PyQt6.QtCore import QTimer
    from ui.main_window import MainWindow

    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")

    violations = []

    # 1. Перехват HTTP-запросов в MainThread
    orig_get = requests.get
    orig_post = requests.post

    def guard_get(*args, **kwargs):
        if threading.current_thread().name == "MainThread":
            violations.append({
                "type": "NETWORK_HTTP_GET",
                "target": args[0] if args else kwargs.get("url", ""),
                "caller": traceback.format_stack()[-2].strip(),
                "stack": traceback.format_stack()[-6:-1]
            })
        return orig_get(*args, **kwargs)

    def guard_post(*args, **kwargs):
        if threading.current_thread().name == "MainThread":
            violations.append({
                "type": "NETWORK_HTTP_POST",
                "target": args[0] if args else kwargs.get("url", ""),
                "caller": traceback.format_stack()[-2].strip(),
                "stack": traceback.format_stack()[-6:-1]
            })
        return orig_post(*args, **kwargs)

    monkeypatch.setattr(requests, "get", guard_get)
    monkeypatch.setattr(requests, "post", guard_post)

    # 2. Перехват блокирующих сокетных подключений в MainThread
    orig_connect = socket.socket.connect

    def guard_connect(self, address):
        if threading.current_thread().name == "MainThread":
            violations.append({
                "type": "BLOCKING_SOCKET_CONNECT",
                "target": str(address),
                "caller": traceback.format_stack()[-2].strip(),
                "stack": traceback.format_stack()[-6:-1]
            })
        return orig_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard_connect)

    # 3. Перехват time.sleep в MainThread (> 50 мс)
    orig_sleep = time.sleep

    def guard_sleep(secs):
        if threading.current_thread().name == "MainThread" and secs > 0.05:
            violations.append({
                "type": "BLOCKING_TIME_SLEEP",
                "target": f"{secs}s",
                "caller": traceback.format_stack()[-2].strip(),
                "stack": traceback.format_stack()[-6:-1]
            })
        return orig_sleep(secs)

    monkeypatch.setattr(time, "sleep", guard_sleep)

    # Холодный запуск окна с реальными фоновыми сервисами (без заглушек)
    t0 = time.perf_counter()
    window = MainWindow()
    t_init = (time.perf_counter() - t0) * 1000

    window.show()

    # Замеряем тики Event Loop
    ticks = []
    def on_tick():
        ticks.append(time.perf_counter() - t0)

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(on_tick)
    timer.start()

    # Прокручиваем цикл событий Qt на 600 мс
    t_end = time.perf_counter() + 0.6
    while time.perf_counter() < t_end:
        qapp.processEvents()
        orig_sleep(0.02)

    timer.stop()

    try:
        # 1. Лимит времени сборки окна
        assert t_init < 1000.0, f"MainWindow init time exceeded 1000ms budget: {t_init:.1f}ms"

        # 2. Плавность цикла событий
        assert len(ticks) >= 3, f"Event loop was frozen during startup! Recorded only {len(ticks)} ticks."

        # 3. Строгий контроль отсутствия блокировок главного потока
        if violations:
            diag_lines = [f"CRITICAL: {len(violations)} blocking call(s) detected in MainThread:"]
            for idx, v in enumerate(violations, 1):
                diag_lines.append(
                    f"\n  [Violation #{idx}] Type: {v['type']}"
                    f"\n    Target : {v['target']}"
                    f"\n    Culprit: {v['caller']}"
                    "\n    Stack Trace:\n      " + "\n      ".join(s.strip() for s in v["stack"])
                )
            assert not violations, "\n".join(diag_lines)
    finally:
        window._shutdown_completed = True
        window.close()
        SandboxManager.set_active_session_id(None)
