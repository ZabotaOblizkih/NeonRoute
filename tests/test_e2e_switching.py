import time
import threading
from unittest.mock import MagicMock
import pytest
from PyQt6.QtCore import QCoreApplication, QTimer

# Инициализируем окружение Qt для обработки сигналов и таймеров
app = QCoreApplication.instance() or QCoreApplication([])

from services.task_queue import TaskQueueWorker, InferenceTask, PRIORITY_LOW, PRIORITY_NORMAL
from services.vram_arbiter import VRAMArbiter, GPUOwner


class TestE2ESwitchingFlow:

    @pytest.fixture(autouse=True)
    def setup_environment(self):
        """Подготовка изолированной рабочей среды перед каждым тестом."""
        # 1. Заглушки (моки) для LM Studio и Forge
        self.mock_lm = MagicMock()
        self.mock_forge = MagicMock()
        
        # Симулируем задержку выгрузки/загрузки в VRAM (100 мс)
        self.mock_lm.unload_models_cli.side_effect = lambda: time.sleep(0.05)
        self.mock_lm.ensure_model_loaded.side_effect = lambda **kw: time.sleep(0.1)
        self.mock_forge.unload_checkpoint.side_effect = lambda: time.sleep(0.05)
        self.mock_forge.is_server_ready.return_value = True

        # 2. Инициализация Арбитра памяти
        self.arbiter = VRAMArbiter.get_instance()
        self.arbiter.set_clients(self.mock_lm, self.mock_forge)
        self.arbiter._current_owner = GPUOwner.NONE

        # 3. Запуск рабочей очереди задач
        self.worker = TaskQueueWorker()
        self.worker.start()

        yield

        # Очистка и остановка очереди после завершения теста
        self.worker.stop()

    def test_e2e_rapid_art_click_during_llm_reload(self):
        """
        Сквозной сценарий:
        1. Генерация первого арта (GPU переходит под Diffusion).
        2. Арт завершен, запускается отложенный возврат LLM в VRAM.
        3. Во время возврата LLM пользователь экстренно нажимает "Сгенерировать новый арт".
        4. Проверяется: отсутствие Deadlock, правильный статус задачи и завершение всех этапов.
        """
        execution_log = []

        def fake_art_generation():
            execution_log.append("art_started")
            time.sleep(0.1)
            execution_log.append("art_finished")
            return {"image_path": "fake_path.png"}

        def fake_llm_reload(**kwargs):
            execution_log.append("llm_reload_started")
            time.sleep(0.2)  # Симуляция тяжелой заливки 7B модели в VRAM
            execution_log.append("llm_reload_finished")
            return True

        def fake_second_art_generation():
            execution_log.append("second_art_started")
            time.sleep(0.1)
            execution_log.append("second_art_finished")
            return {"image_path": "fake_path_2.png"}

        # ── 1. СТАРТ: Первый арт отправляется в очередь ──
        first_art_task = InferenceTask(
            name="🎨 Иллюстрация сцены #1",
            task_type="scene_art",
            func=fake_art_generation
        )
        self.worker.enqueue(first_art_task)

        # Ждем завершения первой генерации (до 1.5 сек с прокачкой событий Qt)
        start_wait = time.time()
        while "art_finished" not in execution_log and time.time() - start_wait < 1.5:
            app.processEvents()
            time.sleep(0.02)

        assert "art_finished" in execution_log, "Первый арт не успел сгенерироваться"

        # ── 2. СИМУЛЯЦИЯ: Ставится задача возврата LLM с правильным task_type='llm' ──
        reload_task = InferenceTask(
            name="Возврат LLM в VRAM",
            task_type="llm",  # Исправленный тип задачи!
            func=fake_llm_reload,
            priority=PRIORITY_LOW
        )
        self.worker.enqueue(reload_task)

        # Даем задаче возврата стартовать
        start_wait = time.time()
        while "llm_reload_started" not in execution_log and time.time() - start_wait < 1.0:
            app.processEvents()
            time.sleep(0.02)

        assert "llm_reload_started" in execution_log, "Возврат LLM не перешел в активное состояние"

        # ── 3. КРИТИЧЕСКИЙ МОМЕНТ: Клик по кнопке второго арта прямо во время загрузки LLM ──
        # Имитируем поведение hardware_engine.py: сброс фоновой памяти и постановка нового арта
        t_click_start = time.time()
        self.worker.abort_tasks_by_type("memory")  # Должен сбрасывать только чат-память!
        t_click_duration = time.time() - t_click_start

        # Проверка на взаимную блокировку (Deadlock)
        assert t_click_duration < 0.3, f"КРИТИЧЕСКИЙ СБОЙ: Deadlock при клике! Задержка: {t_click_duration:.2f}с"

        # Задача LLM НЕ должна быть прервана вызовом abort_tasks_by_type('memory')
        assert not reload_task.is_aborted.is_set(), (
            "ОШИБКА: Задача возврата LLM была ошибочно прервана кнопкой арта! "
            "task_type должен быть 'llm', а не 'memory'."
        )

        # Ставим в очередь второй арт
        second_art_task = InferenceTask(
            name="🎨 Иллюстрация сцены #2",
            task_type="scene_art",
            func=fake_second_art_generation,
            priority=PRIORITY_NORMAL
        )
        self.worker.enqueue(second_art_task)

        # ── 4. ПРОВЕРКА ПОЛНОГО ПЕРЕКЛЮЧЕНИЯ И СИНХРОНИЗАЦИИ ──
        # Дожидаемся завершения второго арта
        start_wait = time.time()
        while "second_art_finished" not in execution_log and time.time() - start_wait < 3.0:
            app.processEvents()
            time.sleep(0.02)

        # Проверяем строгий порядок исполнения
        assert execution_log == [
            "art_started",
            "art_finished",
            "llm_reload_started",
            "llm_reload_finished",
            "second_art_started",
            "second_art_finished"
        ], f"Нарушен детерминированный порядок вызовов: {execution_log}"

        # Проверяем, что арбитр VRAM корректно сменил владельцев
        assert self.arbiter._current_owner == GPUOwner.DIFFUSION
