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
