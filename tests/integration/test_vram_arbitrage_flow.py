"""
Integration tests for VRAMArbiter and memory lifecycle management.
Simulates GPU arbitration contracts between LM Studio (LLM) and Stable Diffusion WebUI Forge (Diffusion).
Validates:
1. Sequential ownership switching (LLM -> DIFFUSION -> LLM).
2. Elimination of redundant unloading when task type does not change.
3. Deferred LLM release for Roleplay visual intent prompt generation (needs_llm_prompt_gen).
4. Resilience when Forge or LM Studio is offline or raises exceptions.
"""
from unittest.mock import MagicMock
import pytest

from services.vram_arbiter import VRAMArbiter, GPUOwner


def test_vram_arbiter_sequential_switching():
    """Сквозной тест монопольного владения VRAM: LLM <-> Diffusion."""
    mock_lm = MagicMock()
    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)
    assert arbiter._current_owner == GPUOwner.NONE

    # 1. Первая текстовая задача (LLM)
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == GPUOwner.LLM
    assert mock_forge.unload_checkpoint.call_count == 1
    assert mock_forge.free_vram.call_count == 1

    # 2. Повторная текстовая задача: не должна дергать Forge повторно (idempotency)
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == GPUOwner.LLM
    assert mock_forge.unload_checkpoint.call_count == 1

    # 3. Задача генерации арта (Diffusion)
    arbiter.prepare_for_task("image")
    assert arbiter._current_owner == GPUOwner.DIFFUSION
    assert mock_lm.unload_models_cli.call_count == 1
    assert mock_forge._wait_for_vram_clear.call_count == 1

    # 4. Возврат к текстовому диалогу (LLM)
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == GPUOwner.LLM
    assert mock_forge.unload_checkpoint.call_count == 2
    assert mock_forge.free_vram.call_count == 2


def test_vram_arbiter_deferred_prompt_generation_guard():
    """
    Проверка сценария Roleplay Scene Art:
    если задаче сначала требуется сгенерировать промпт через LLM (needs_llm_prompt_gen=True),
    выгрузка LLM должна быть отложена, чтобы LLM успела составить промпт.
    """
    mock_lm = MagicMock()
    mock_forge = MagicMock()

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == GPUOwner.LLM

    # Поступает задача диффузии, требующая предварительной генерации промпта
    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": True})
    assert arbiter._current_owner == GPUOwner.LLM
    assert not mock_lm.unload_models_cli.called

    # После того как промпт сгенерирован, задача переходит в чистый image inference:
    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": False})
    assert arbiter._current_owner == GPUOwner.DIFFUSION
    assert mock_lm.unload_models_cli.called


def test_vram_arbiter_offline_forge_resilience():
    """Проверка бессбойной работы арбитра, если сервер Forge недоступен или падает."""
    mock_lm = MagicMock()
    mock_forge = MagicMock()
    mock_forge.is_server_ready.side_effect = RuntimeError("Forge connection refused")

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)

    # Не должно выбрасывать исключений при падении проверки Forge
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == GPUOwner.LLM
    assert not mock_forge.unload_checkpoint.called


def test_vram_arbiter_cpu_and_service_tasks_neutrality():
    """Сервисные задачи (sandbox, memory, tts) не меняют владельца VRAM."""
    mock_lm = MagicMock()
    mock_forge = MagicMock()

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == GPUOwner.LLM

    arbiter.prepare_for_task("memory")
    assert arbiter._current_owner == GPUOwner.LLM

    arbiter.prepare_for_task("tts")
    assert arbiter._current_owner == GPUOwner.LLM
    assert not mock_lm.unload_models_cli.called


def test_vram_arbiter_idempotent_llm_and_forge_unloads():
    """Повторные вызовы release_llm_for_diffusion и _release_forge_vram не дублируют выгрузку."""
    mock_lm = MagicMock()
    mock_lm._is_model_unloaded = False
    mock_lm.process_manager = None

    def _do_unload():
        mock_lm._is_model_unloaded = True
        return True

    mock_lm.unload_models_cli.side_effect = _do_unload

    mock_forge = MagicMock()
    mock_forge._checkpoint_unloaded = False
    mock_forge.is_server_ready.return_value = True

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)

    # 1. Двойной вызов release_llm_for_diffusion -> реальная выгрузка LLM только 1 раз
    assert arbiter.release_llm_for_diffusion() is True
    assert arbiter.release_llm_for_diffusion() is True
    assert mock_lm.unload_models_cli.call_count == 1
    assert mock_forge._wait_for_vram_clear.call_count == 1

    # 2. Если ForgeClient уже выгрузил чекпоинт сам (_checkpoint_unloaded=True), _release_forge_vram не шлёт запросы
    mock_forge._checkpoint_unloaded = True
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == GPUOwner.LLM
    mock_forge.unload_checkpoint.assert_not_called()
    mock_forge.free_vram.assert_not_called()


def test_vram_arbiter_schedule_and_cancel_llm_reload(monkeypatch):
    """Единый headless-возврат LLM в VRAM: постановка задачи в TaskQueue и отмена при новой задаче."""
    mock_lm = MagicMock()
    mock_lm.process_manager = None
    mock_lm.ensure_model_loaded = MagicMock()
    mock_tq = MagicMock()
    mock_tq._running = True
    mock_tq.current_task = None
    mock_tq.has_pending_tasks.return_value = False

    monkeypatch.setattr("services.task_queue.get_task_queue", lambda: mock_tq)
    monkeypatch.setattr(
        "services.vram_arbiter.resolve_active_llm_target",
        lambda session_id=None, mode=None, fallback_model=None: ("test-model.gguf", 14336, False, False),
    )

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=MagicMock())
    arbiter._current_owner = GPUOwner.NONE

    # 1. Отмена сбрасывает таймер до срабатывания
    assert arbiter.schedule_llm_reload(delay=0.5, session_id="s1") is True
    assert arbiter.cancel_llm_reload(reason="test") is True
    assert mock_tq.enqueue.call_count == 0

    # 2. Срабатывание таймера ставит тихую задачу Возврат LLM в VRAM
    assert arbiter.schedule_llm_reload(delay=0.05, session_id="s1") is True
    import time
    time.sleep(0.15)
    assert mock_tq.enqueue.call_count == 1
    enqueued_task = mock_tq.enqueue.call_args[0][0]
    assert enqueued_task.name == "Возврат LLM в VRAM"
    assert enqueued_task.task_type == "llm"
    assert enqueued_task.metadata.get("silent") is True
    assert enqueued_task.metadata.get("is_llm_reload") is True


def test_vram_arbiter_stabilization_plateau_at_780mb(monkeypatch):
    """VRAMArbiter._wait_for_vram_stabilization exits via plateau (< 1500 MB and delta < 15 MB)."""
    import time
    from services.layer_budgeter import LayerBudgeter

    arbiter = VRAMArbiter.get_instance()
    readings = iter([780.0, 778.5, 778.1, 778.1, 778.1, 778.1, 778.1])
    monkeypatch.setattr(LayerBudgeter, "get_current_used_vram_mb", lambda: next(readings, 778.1))

    start_t = time.monotonic()
    arbiter._wait_for_vram_stabilization(timeout_s=1.0, step_s=0.01, dwell_s=0.05)
    assert (time.monotonic() - start_t) < 0.5

