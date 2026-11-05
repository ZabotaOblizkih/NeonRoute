import pytest
from pathlib import Path
from unittest.mock import patch

def test_01_sandbox_manager_paths(tmp_path):
    from services.sandbox_manager import SandboxManager
    
    # 1. Защита от Path Traversal (clean_workspace_filename)
    assert SandboxManager.clean_workspace_filename("../../../../windows/system32/cmd.exe") == "windows/system32/cmd.exe"
    assert SandboxManager.clean_workspace_filename("workspace/app.py") == "app.py"
    
    # 2. Изоляция файловой системы (validate_read_path)
    with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=tmp_path):
        try:
            SandboxManager.validate_read_path("C:/Windows/System32")
            assert False, "Должен был заблокировать системную папку"
        except PermissionError:
            pass
            
        # Успешное чтение валидного файла
        dummy = tmp_path / "test.txt"
        dummy.write_text("hello", encoding="utf-8")
        res = SandboxManager.validate_read_file_path(str(dummy))
        assert res == dummy.resolve()

def test_02_tool_parser():
    from services.tool_parser import ToolParser
    
    # Парсинг с дополнительными мусорными аргументами
    raw_text = '<<TOOL:write_file filename="test.py" extra="123">>print("OK")<</TOOL>>'
    calls = ToolParser.parse_tool_calls(raw_text)
    assert len(calls) == 1
    assert calls[0].name == "write_file"
    assert calls[0].params["filename"] == "test.py"
    
    # Нормализация оборванного тега
    broken = '<<TOOL:run_script filename="app.py">'
    calls_broken = ToolParser.parse_tool_calls(broken)
    assert calls_broken[0].name == "run_script"
    
    # Очистка текста от тегов (со стандартным синтаксисом)
    std_text = 'Привет! <<TOOL:write_file filename="test.py">>\nprint("OK")\n<</TOOL>> Конец.'
    clean_text = ToolParser.strip_tool_tags(std_text)
    assert "Привет!" in clean_text and "Конец." in clean_text
    assert "TOOL" not in clean_text
    assert "print" not in clean_text

def test_03_task_queue():
    from services.task_queue import TaskQueueWorker, InferenceTask, PRIORITY_HIGH, PRIORITY_LOW
    
    queue = TaskQueueWorker()
    
    t_low = InferenceTask("low", "memory", lambda: None, priority=PRIORITY_LOW)
    t_high = InferenceTask("high", "llm", lambda: None, priority=PRIORITY_HIGH)
    
    # Добавляем сначала низкий приоритет, потом высокий
    queue.enqueue(t_low)
    queue.enqueue(t_high)
    
    # В PriorityQueue первым должен выйти объект с наименьшим числом (PRIORITY_HIGH = 0)
    item1 = queue._queue.get()
    item2 = queue._queue.get()
    
    assert item1[2].name == "high"
    assert item2[2].name == "low"
    assert queue.has_pending_tasks(("memory", "llm")) is False
