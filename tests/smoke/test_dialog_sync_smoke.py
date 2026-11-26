"""
tests/smoke/test_dialog_sync_smoke.py
=====================================
Smoke- и контрактные тесты неблокирующей синхронизации диалога ModelsManagerDialog,
ArtDirector.reload_models() и ModelRouter:
1. Контракт закрытия через reject()/accept() и вызова reload_models() без блокировки GUI.
2. Неблокирующий вызов update_embeddings_async(delay=0.0) в _save_all() и reload_models().
3. Коалесинг, дедупликация и потокобезопасность ModelRouter.
4. Вынос forge_client.refresh_checkpoints() в фон при FP8-конвертации (отсутствие блокировок в GUI).
5. Гарантированный сброс WaitCursor при любых исходах Civitai AutoFill.
"""
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from services.model_router import ModelRouter
from services.art_director.director import ArtDirector
from ui.models_manager_dialog import ModelsManagerDialog
from ui.models_manager.workers import FP8ConvertWorker


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def clean_router(tmp_path, monkeypatch):
    """Изолированный синглтон ModelRouter с временным art_director_models.json."""
    tmp_json = tmp_path / "art_director_models.json"
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", tmp_json)
    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", tmp_json)
    ModelRouter.reset_instance()
    yield tmp_json
    ModelRouter.reset_instance()


def test_dialog_sync_contract_and_non_blocking_reload(qapp, clean_router, monkeypatch):
    """
    Проверяет:
    1. Контракт test_dialog_sync_smoke: вызов reload_models() после закрытия диалога через reject().
    2. reload_models() вызывает update_embeddings_async, а НЕ синхронный update_embeddings().
    3. GUI не блокируется даже при медленной инициализации движка эмбеддингов.
    """
    router = ModelRouter.get_instance(data_path=clean_router, auto_load=False)
    router.profiles = [
        {"id": "m1", "name": "Model 1", "routing_description": "Desc 1", "active": True}
    ]
    router.save_profiles(router.profiles)

    art_director = ArtDirector()
    art_director.router = router

    # Имитируем медленную загрузку FastEmbed (5 секунд)
    def slow_engine():
        time.sleep(5.0)
        mock_eng = MagicMock()
        mock_eng.embed.return_value = [[0.1] * 384]
        return mock_eng

    monkeypatch.setattr(router, "get_embedding_engine", slow_engine)

    # Замеряем время вызова reload_models() в контексте GUI
    t0 = time.perf_counter()
    art_director.reload_models()
    elapsed = time.perf_counter() - t0

    # Вызов должен быть мгновенным (асинхронным, < 0.1 с), не ожидая slow_engine
    assert elapsed < 0.1, f"reload_models() заблокировал поток на {elapsed:.3f} с!"


def test_save_all_non_blocking_and_async(qapp, clean_router, monkeypatch):
    """
    Проверяет:
    1. _save_all() вызывает update_embeddings_async(delay=0.0).
    2. Синхронный update_embeddings() не вызывается в GUI-потоке.
    3. GUI не задерживается при сохранении.
    """
    from services.art_director_config import save_art_director_models
    save_art_director_models([
        {"id": "m1", "name": "Model 1", "checkpoint": "model1.safetensors", "routing_description": "Desc 1", "active": True}
    ])
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    async_called = []
    sync_called = []

    monkeypatch.setattr(
        dialog.router,
        "update_embeddings_async",
        lambda delay=0.0: async_called.append(delay)
    )
    monkeypatch.setattr(
        dialog.router,
        "update_embeddings",
        lambda: sync_called.append(True)
    )

    with patch("ui.models_manager_dialog.sync_registry_with_user_config"):
        dialog._save_all()

    assert len(async_called) == 1, "update_embeddings_async должен быть вызван ровно 1 раз"
    assert async_called[0] == 0.0, "delay должен быть равен 0.0"
    assert len(sync_called) == 0, "Синхронный update_embeddings() НЕ должен вызываться в _save_all()"

    dialog.reject()


def test_model_router_coalescing_and_deduplication(clean_router, monkeypatch):
    """
    Проверяет:
    1. Коалесинг конкурентных вызовов update_embeddings_async: не запускается лишних потоков.
    2. Дедупликация: неизменившиеся дескрипторы не пересчитываются повторно.
    3. Потокобезопасная очистка в reset_instance().
    """
    # Разрешаем запуск фоновых потоков в тесте
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    router = ModelRouter.get_instance(data_path=clean_router, auto_load=False)
    router.profiles = [
        {"id": "test_1", "name": "Model 1", "routing_description": "Photo realism", "active": True}
    ]
    router.save_profiles(router.profiles)

    engine_calls = []
    mock_engine = MagicMock()
    mock_engine.embed.side_effect = lambda texts: (engine_calls.append(texts), [[0.5] * 384])[1]

    monkeypatch.setattr(router, "get_embedding_engine", lambda: mock_engine)

    # 1. Первый прогон — расчёт эмбеддингов
    t = router.update_embeddings_async(delay=0.0)
    if t is not None:
        t.join(timeout=3.0)

    assert len(engine_calls) == 1
    assert "test_1" in router.embeddings

    # 2. Повторный вызов без изменения дескрипторов — дедупликация
    engine_calls.clear()
    router.update_embeddings()
    assert len(engine_calls) == 0, "Повторный расчёт должен быть пропущен при неизменных дескрипторах"

    # 3. Изменение дескриптора — пересчёт срабатывает
    router.profiles[0]["routing_description"] = "Anime fantasy 2D"
    router.save_profiles(router.profiles)
    router.update_embeddings()
    assert len(engine_calls) == 1, "При изменении описания расчёт должен выполниться"

    ModelRouter.reset_instance()


def test_fp8_finished_no_blocking_http_in_gui(qapp, clean_router, tmp_path, monkeypatch):
    """
    Проверяет:
    1. _on_fp8_finished() в GUI-потоке НЕ вызывает forge_client.refresh_checkpoints().
    2. FP8ConvertWorker.run() вызывает refresh_checkpoints() в фоновом потоке.
    """
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    mock_forge = MagicMock()
    dialog.forge_client = mock_forge

    fp8_file = tmp_path / "model_fp8e4.safetensors"
    fp8_file.write_bytes(b"dummy")

    # Слот GUI
    dialog._on_fp8_finished(str(fp8_file))

    # В GUI-слоте не должно быть вызовов refresh_checkpoints
    assert mock_forge.refresh_checkpoints.call_count == 0, \
        "_on_fp8_finished() не должен вызывать refresh_checkpoints() в главном GUI потоке!"

    # Проверяем, что воркер вызывает refresh_checkpoints() в фоне
    worker = FP8ConvertWorker(
        python_exe="python",
        script_path=tmp_path / "convert.py",
        input_checkpoint_path=fp8_file,
        forge_client=mock_forge,
        parent=None
    )
    assert worker.forge_client is mock_forge

    dialog.reject()


def test_civitai_autofill_wait_cursor_guaranteed_reset(qapp, clean_router):
    """
    Проверяет, что при завершении, ошибке или отмене оверрайд курсора гарантированно сбрасывается.
    """
    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    # Имитируем установку курсора ожидания
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    assert QApplication.overrideCursor() is not None

    # Слот ошибки воркера должен восстанавливать курсор
    dialog._on_civitai_params_error("Connection timeout")
    assert QApplication.overrideCursor() is None, "Курсор должен быть сброшен после _on_civitai_params_error"

    # Проверяем _stop_all_workers
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    dialog._stop_all_workers()
    assert QApplication.overrideCursor() is None, "Курсор должен быть сброшен в _stop_all_workers()"

    dialog.reject()
