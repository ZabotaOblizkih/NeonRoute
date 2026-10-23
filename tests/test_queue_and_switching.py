import time
import threading
import pytest
from PyQt6.QtCore import QCoreApplication

# Инициализация минимального контекста Qt для работы сигналов QThread в тестах
_app = QCoreApplication.instance() or QCoreApplication([])

from services.task_queue import TaskQueueWorker, InferenceTask, PRIORITY_LOW, PRIORITY_HIGH
from services.vram_arbiter import VRAMArbiter, GPUOwner


class TestTaskQueueAndVRAM:

    def test_01_lock_is_reentrant(self):
        """
        Проверка типа замка:
        Замок должен быть RLock, чтобы предотвратить взаимную блокировку
        при вызове abort_current() изнутри abort_tasks_by_type().
        """
        worker = TaskQueueWorker()
        assert isinstance(worker._lock, type(threading.RLock())), (
            "КРИТИЧЕСКИЙ БАГ: worker._lock должен быть threading.RLock(), "
            "иначе abort_tasks_by_type вызовет Deadlock интерфейса!"
        )

    def test_02_abort_tasks_by_type_does_not_deadlock(self):
        """
        Проверка отсутствия Deadlock:
        Имитирует выполнение задачи и вызывает abort_tasks_by_type().
        Метод должен завершиться мгновенно (меньше чем за 0.5 секунды).
        """
        worker = TaskQueueWorker()
        worker.start()

        task_running = threading.Event()
        task_can_exit = threading.Event()

        def slow_dummy_task():
            task_running.set()
            task_can_exit.wait(timeout=2.0)
            return True

        task = InferenceTask(
            name="Тестовая задача",
            task_type="memory",
            func=slow_dummy_task
        )
        worker.enqueue(task)

        # Ждем, пока задача реально начнет выполняться
        assert task_running.wait(timeout=1.0), "Задача не стартовала вовремя"

        # Замеряем время вызова отмены
        start_t = time.time()
        worker.abort_tasks_by_type("memory")
        elapsed = time.time() - start_t

        task_can_exit.set()
        worker.stop()

        assert elapsed < 0.5, (
            f"ОБНАРУЖЕН DEADLOCK: abort_tasks_by_type выполнялся {elapsed:.2f} сек. "
            "Поток заблокировал сам себя на self._lock!"
        )

    def test_03_abort_memory_does_not_affect_llm_task(self):
        """
        Проверка изоляции типов:
        Отмена фоновых задач памяти (memory) не должна затрагивать задачи типа 'llm'.
        """
        worker = TaskQueueWorker()
        worker.start()

        task_running = threading.Event()
        task_can_exit = threading.Event()

        def dummy_llm_load():
            task_running.set()
            task_can_exit.wait(timeout=2.0)
            return True

        llm_task = InferenceTask(
            name="Возврат LLM в VRAM",
            task_type="llm",
            func=dummy_llm_load,
            priority=PRIORITY_LOW
        )
        worker.enqueue(llm_task)

        assert task_running.wait(timeout=1.0), "Задача LLM не запустилась"

        # Имитируем клик по кнопке генерации арта (сброс фоновой текстовой памяти)
        worker.abort_tasks_by_type("memory")

        # Задача LLM НЕ должна быть помечена как отмененная
        assert not llm_task.is_aborted.is_set(), (
            "ОШИБКА РАССИНХРОНА: Задача 'Возврат LLM в VRAM' была прервана вызовом "
            "abort_tasks_by_type('memory')! Убедитесь, что task_type установлен в 'llm'."
        )

        task_can_exit.set()
        worker.stop()

    def test_04_hardware_engine_contract_check(self):
        """
        Статическая проверка файла hardware_engine.py:
        Гарантирует, что никто случайно не вернул task_type='memory' для возврата LLM.
        """
        import inspect
        from ui.controllers.hardware_engine import HardwareEngineMixin

        source = inspect.getsource(HardwareEngineMixin._execute_lms_reload)
        
        assert 'name="Возврат LLM в VRAM"' in source, "Не найден блок задачи 'Возврат LLM в VRAM'"
        assert 'task_type="llm"' in source, (
            "РЕГРЕССИЯ: В hardware_engine.py в методе _execute_lms_reload() "
            "параметр task_type снова изменен на что-то отличное от 'llm'!"
        )
