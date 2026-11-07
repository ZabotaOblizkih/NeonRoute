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

def test_startup_non_blocking_guard(qapp, monkeypatch, tmp_path):
    # Изоляция истории для бенчмарк-теста холодного старта:
    clean_history_dir = tmp_path / "clean_bench_history"
    clean_history_dir.mkdir(parents=True, exist_ok=True)
    import config
    import services.session_manager as sm
    import services.rp_cast_manager as rcm
    monkeypatch.setattr(config, "HISTORY_DIR", clean_history_dir)
    monkeypatch.setattr(sm, "HISTORY_DIR", clean_history_dir, raising=False)
    monkeypatch.setattr(rcm, "HISTORY_DIR", clean_history_dir, raising=False)
    if hasattr(sm.SessionManager, "HISTORY_DIR"):
        monkeypatch.setattr(sm.SessionManager, "HISTORY_DIR", clean_history_dir, raising=False)

    """Сторожевой смок-тест: проверяет отсутствие блокировок в GUI-потоке,
    детектирует микрофризы кадров (Jitter Gate) и пресекает преждевременный
    прогрев тяжелых векторов FastEmbed в фазе старта LLM.
    """
    import time
    import threading
    import traceback
    import requests
    import socket
    from PyQt6.QtCore import QTimer
    from services.sandbox_manager import SandboxManager

    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")

    violations = []

    # 1. Хуки сетевых вызовов в MainThread
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

    # 2. Хук блокирующих сокетов в MainThread
    orig_connect = socket.socket.connect
    def guard_connect(self, address):
        if threading.current_thread().name == "MainThread":
            violations.append({"type": "SOCKET_CONNECT", "target": str(address), "caller": traceback.format_stack()[-2].strip()})
        return orig_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard_connect)

    # 3. Ловушка преждевременной компиляции векторов FastEmbed (< 4.0s)
    t_test_start = time.perf_counter()
    early_vector_inits = []
    import services.memory_db as mdb
    orig_get_engine = mdb._get_embedding_engine

    def guard_get_engine(*args, **kwargs):
        elapsed = time.perf_counter() - t_test_start
        if elapsed < 4.0:
            early_vector_inits.append({
                "elapsed_s": elapsed,
                "caller": traceback.format_stack()[-2].strip()
            })
        return orig_get_engine(*args, **kwargs)

    monkeypatch.setattr(mdb, "_get_embedding_engine", guard_get_engine)

    # 4. Импорт и сборка окна
    from ui.main_window import MainWindow

    t_win_start = time.perf_counter()
    window = MainWindow()
    t_win = (time.perf_counter() - t_win_start) * 1000
    window.show()

    # 5. Детектор микрофризов кадров (Frame Jitter)
    intervals = []
    last_tick = time.perf_counter()

    def on_tick():
        nonlocal last_tick
        now = time.perf_counter()
        gap = (now - last_tick) * 1000
        last_tick = now
        intervals.append(gap)

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(on_tick)
    timer.start()

    # Прокручиваем цикл событий на 800 мс
    t_end = time.perf_counter() + 0.8
    while time.perf_counter() < t_end:
        qapp.processEvents()
        time.sleep(0.015)

    timer.stop()

    frame_gaps = intervals[1:] if len(intervals) > 1 else [0]
    max_gap = max(frame_gaps) if frame_gaps else 0

    try:
        # Проверки качества старта:
        assert t_win < 1500.0, f"MainWindow constructor too slow: {t_win:.1f}ms > 1500ms"
        assert len(intervals) >= 4, f"Event loop stalled! Only {len(intervals)} ticks recorded."
        assert max_gap < 300.0, f"UI Stutter detected! Frame gap was {max_gap:.1f}ms > 300ms."
        assert not violations, f"MainThread blocking calls detected: {violations}"
        assert not early_vector_inits, f"Premature FastEmbed compilation detected in startup phase: {early_vector_inits}"
    finally:
        window._shutdown_completed = True
        window.close()
        SandboxManager.set_active_session_id(None)

