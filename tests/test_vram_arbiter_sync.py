import inspect
from unittest.mock import MagicMock
from services.vram_arbiter import VRAMArbiter, GPUOwner
from services.task_queue import TaskQueueWorker, InferenceTask
import ui.controllers.hardware_engine as hardware_engine


def test_prepare_for_task_signature():
    sig = inspect.signature(VRAMArbiter.prepare_for_task)
    params = list(sig.parameters.keys())
    assert params == ["self", "task_type", "metadata"]
    assert sig.parameters["metadata"].default is None


def test_vram_arbiter_prepare_for_diffusion_tasks():
    mock_lm = MagicMock()
    mock_lm.unload_models_cli = MagicMock()

    mock_forge = MagicMock()
    mock_forge._wait_for_vram_clear = MagicMock()

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)

    for t_type in ("image", "scene_art", "scene_background"):
        mock_lm.unload_models_cli.reset_mock()
        mock_forge._wait_for_vram_clear.reset_mock()
        arbiter._current_owner = GPUOwner.LLM

        arbiter.prepare_for_task(t_type)

        mock_lm.unload_models_cli.assert_called_once()
        mock_forge._wait_for_vram_clear.assert_called_once()
        assert arbiter._current_owner == GPUOwner.DIFFUSION


def test_task_queue_calls_prepare_for_task():
    arbiter = VRAMArbiter.get_instance()
    mock_prepare = MagicMock()
    original_prepare = arbiter.prepare_for_task
    arbiter.prepare_for_task = mock_prepare

    try:
        worker = TaskQueueWorker()
        executed = []

        def sample_task():
            executed.append("done")
            return {"success": True}

        task = InferenceTask(
            name="Test Task",
            task_type="scene_art",
            func=sample_task,
            metadata={"test": 123}
        )

        worker.enqueue(task)
        worker.start()
        worker.wait(1500)
        worker.stop()

        mock_prepare.assert_called_once_with("scene_art", metadata={"test": 123})
        assert "done" in executed
    finally:
        arbiter.prepare_for_task = original_prepare


def test_scene_art_pipeline_no_duplicate_unload_model():
    source = inspect.getsource(hardware_engine.HardwareEngineMixin._on_scene_art_requested)
    # Ensure there is no self.lm_client.unload_model() inside _on_scene_art_requested / _scene_art_pipeline
    assert "self.lm_client.unload_model()" not in source
    # Ensure release_llm_for_diffusion is called explicitly
    assert "VRAMArbiter.get_instance().release_llm_for_diffusion()" in source
    assert '"needs_llm_prompt_gen": needs_llm_gen' in source


def test_task_queue_abort_tasks_by_type_no_deadlock():
    import threading
    worker = TaskQueueWorker()
    # Check that worker._lock is RLock (threading._RLock or threading.RLock)
    assert type(worker._lock) is type(threading.RLock())

    # Simulate an active task matching the aborted type
    task = InferenceTask(name="Active Memory Task", task_type="memory", func=lambda: None)
    worker._current_task = task

    # Calling abort_tasks_by_type must not deadlock
    worker.abort_tasks_by_type("memory")
    assert task.is_aborted.is_set()


def test_execute_lms_reload_task_type():
    from services.task_queue import PRIORITY_LOW

    class DummyEngine(hardware_engine.HardwareEngineMixin):
        def __init__(self):
            self.task_queue = MagicMock()
            self.task_queue.current_task = None
            self.task_queue.has_pending_tasks.return_value = False
            self.lm_client = MagicMock()
            self.active_lms_model = "test-model-qwen"
            self.current_mode = "assistant"
            self._active_tasks = {}

        def is_engineer_mode_active(self):
            return False

    engine = DummyEngine()
    engine._execute_lms_reload()

    assert engine.task_queue.enqueue.called
    enqueued_task = engine.task_queue.enqueue.call_args[0][0]
    assert enqueued_task.name == "Возврат LLM в VRAM"
    assert enqueued_task.task_type == "llm"
    assert enqueued_task.priority == PRIORITY_LOW

