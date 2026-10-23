"""
Тесты жизненного цикла VRAMArbiter (services/vram_arbiter.py).
Проверка монопольного переключения GPU, выгрузки моделей в RAM
и устойчивости к сетевым сбоям Forge/LM Studio.
"""

import pytest
from unittest.mock import MagicMock
from services.vram_arbiter import VRAMArbiter, GPUOwner


@pytest.fixture(autouse=True)
def reset_arbiter_singleton():
    """Сбрасывает синглтон перед каждым тестом для полной изоляции."""
    VRAMArbiter._instance = None
    yield
    VRAMArbiter._instance = None


@pytest.fixture
def mock_clients():
    """Создает моки клиентов LM Studio и SD WebUI Forge."""
    lm = MagicMock()
    lm.unload_models_cli = MagicMock()

    forge = MagicMock()
    forge.is_server_ready = MagicMock(return_value=True)
    forge.unload_checkpoint = MagicMock()
    forge.free_vram = MagicMock()
    forge._wait_for_vram_clear = MagicMock()

    return lm, forge


class TestVRAMArbiterStateTransitions:
    """Проверка переходов состояний владения GPU."""

    def test_initial_state_is_none(self):
        arbiter = VRAMArbiter()
        assert arbiter._current_owner == GPUOwner.NONE

    def test_switch_to_llm_from_none(self, mock_clients):
        lm, forge = mock_clients
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)

        arbiter.prepare_for_task("llm")

        assert arbiter._current_owner == GPUOwner.LLM
        # Проверяем, что была попытка освободить память Forge
        forge.is_server_ready.assert_called_once_with(timeout=0.3)
        forge.unload_checkpoint.assert_called_once()
        forge.free_vram.assert_called_once()

    def test_switch_to_llm_via_ephemeral_doc_flag(self, mock_clients):
        lm, forge = mock_clients
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)

        # Любая задача с флагом is_ephemeral_doc должна передавать GPU языковой модели
        arbiter.prepare_for_task("custom_task", metadata={"is_ephemeral_doc": True})

        assert arbiter._current_owner == GPUOwner.LLM

    def test_llm_tasks_do_not_retrigger_forge_release_if_already_owner(self, mock_clients):
        lm, forge = mock_clients
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)

        # Первый вызов переключает на LLM
        arbiter.prepare_for_task("llm")
        assert forge.unload_checkpoint.call_count == 1

        # Повторные текстовые задачи не должны дергать Forge повторно
        arbiter.prepare_for_task("translation")
        arbiter.prepare_for_task("file")
        assert forge.unload_checkpoint.call_count == 1
        assert arbiter._current_owner == GPUOwner.LLM

    def test_switch_to_diffusion_unloads_llm(self, mock_clients):
        lm, forge = mock_clients
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)

        # Запускаем генерацию изображения
        arbiter.prepare_for_task("scene_art")

        assert arbiter._current_owner == GPUOwner.DIFFUSION
        # Языковая модель обязана синхронно выгрузиться из VRAM
        lm.unload_models_cli.assert_called_once()
        forge._wait_for_vram_clear.assert_called_once()

    def test_switch_to_diffusion_skips_unload_when_needs_llm_prompt_gen(self, mock_clients):
        lm, forge = mock_clients
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)
        arbiter._current_owner = GPUOwner.LLM

        arbiter.prepare_for_task("scene_art", metadata={"needs_llm_prompt_gen": True})

        # LLM не должна выгружаться до генерации промта
        lm.unload_models_cli.assert_not_called()
        forge._wait_for_vram_clear.assert_not_called()
        assert arbiter._current_owner == GPUOwner.LLM

    def test_release_llm_for_diffusion(self, mock_clients):
        lm, forge = mock_clients
        forge._last_vram_wait_ts = 1234.5
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)
        arbiter._current_owner = GPUOwner.LLM

        arbiter.release_llm_for_diffusion()

        lm.unload_models_cli.assert_called_once()
        assert forge._last_vram_wait_ts == 0.0
        forge._wait_for_vram_clear.assert_called_once()
        assert arbiter._current_owner == GPUOwner.DIFFUSION

    def test_cpu_and_passive_tasks_do_not_change_owner(self, mock_clients):
        lm, forge = mock_clients
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)
        arbiter._current_owner = GPUOwner.LLM

        # Задачи TTS, памяти или песочницы не должны сбрасывать владельца GPU
        arbiter.prepare_for_task("tts")
        arbiter.prepare_for_task("memory_extraction")

        assert arbiter._current_owner == GPUOwner.LLM
        lm.unload_models_cli.assert_not_called()


class TestForgeVRAMReleaseResilience:
    """Проверка отказоустойчивости при сбоях Forge API."""

    def test_release_forge_when_client_is_none(self):
        arbiter = VRAMArbiter(lm_client=None, forge_client=None)
        # Не должно вызывать исключений (AttributeError)
        arbiter.prepare_for_task("llm")
        assert arbiter._current_owner == GPUOwner.LLM

    def test_release_forge_when_server_not_ready(self, mock_clients):
        lm, forge = mock_clients
        forge.is_server_ready.return_value = False
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)

        arbiter.prepare_for_task("llm")

        # Если Forge офлайн, команды выгрузки не отправляются
        forge.unload_checkpoint.assert_not_called()
        forge.free_vram.assert_not_called()
        assert arbiter._current_owner == GPUOwner.LLM

    def test_release_forge_handles_network_timeout_gracefully(self, mock_clients):
        lm, forge = mock_clients
        # Имитируем зависание или обрыв сети
        forge.is_server_ready.side_effect = TimeoutError("Connection timed out")
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)

        arbiter.prepare_for_task("llm")

        forge.unload_checkpoint.assert_not_called()
        assert arbiter._current_owner == GPUOwner.LLM

    def test_release_forge_catches_api_errors_during_unload(self, mock_clients):
        lm, forge = mock_clients
        forge.is_server_ready.return_value = True
        forge.unload_checkpoint.side_effect = RuntimeError("Forge internal error")
        arbiter = VRAMArbiter(lm_client=lm, forge_client=forge)

        # Ошибка внутри Forge не должна ронять рабочий поток
        arbiter.prepare_for_task("llm")
        assert arbiter._current_owner == GPUOwner.LLM


class TestArbiterSingletonAndClients:
    """Проверка методов регистрации клиентов и синглтона."""

    def test_singleton_get_instance(self):
        inst1 = VRAMArbiter.get_instance()
        inst2 = VRAMArbiter.get_instance()
        assert inst1 is inst2

    def test_set_clients_updates_references(self):
        arbiter = VRAMArbiter()
        dummy_lm = object()
        dummy_forge = object()

        arbiter.set_clients(dummy_lm, dummy_forge)

        assert arbiter.lm_client is dummy_lm
        assert arbiter.forge_client is dummy_forge