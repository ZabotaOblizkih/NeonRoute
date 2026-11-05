import pytest
from pathlib import Path
from unittest.mock import patch

def test_01_sandbox_manager_paths():
    from services.sandbox_manager import SandboxManager
    
    # 1. Защита от Path Traversal (усечение выходов на уровень вверх)
    assert SandboxManager.clean_workspace_filename("../../../../windows/system32/cmd.exe") == "windows/system32/cmd.exe"
    
    # 2. Изоляция системных папок на чтение
    try:
        SandboxManager.validate_read_path("C:/Windows/System32")
        assert False, "Должен был заблокировать системную папку"
    except PermissionError:
        pass

    # 3. 100% In-Memory валидация без обращения к диску
    fake_base = Path("C:/FakeNeonRoute").resolve()
    fake_file = fake_base / "test.txt"

    with patch("services.sandbox_manager.BASE_DIR", fake_base), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("pathlib.Path.stat") as mock_stat:
         
        # Заглушка: файл "весит" 1024 байта, чтобы пройти лимит в 5 МБ
        mock_stat.return_value.st_size = 1024 
        
        # Песочница прогонит все проверки безопасности, не трогая диск!
        res = SandboxManager.validate_read_file_path(str(fake_file))
        assert res.name == "test.txt"

def test_02_tool_parser():
    from services.tool_parser import ToolParser
    raw_text = '<<TOOL:write_file filename="test.py" extra="123">>print("OK")<</TOOL>>'
    calls = ToolParser.parse_tool_calls(raw_text)
    assert calls[0].name == "write_file"
    assert calls[0].params["filename"] == "test.py"
    
    clean_text = ToolParser.strip_tool_tags('Привет! <<TOOL:write_file filename="test.py">>\nprint("OK")\n<</TOOL>> Конец.')
    assert "Привет!" in clean_text and "Конец." in clean_text
    assert "TOOL" not in clean_text

def test_03_task_queue():
    from services.task_queue import TaskQueueWorker, InferenceTask, PRIORITY_HIGH, PRIORITY_LOW
    queue = TaskQueueWorker()
    
    t_low = InferenceTask("low", "memory", lambda: None, priority=PRIORITY_LOW)
    t_high = InferenceTask("high", "llm", lambda: None, priority=PRIORITY_HIGH)
    
    queue.enqueue(t_low)
    queue.enqueue(t_high)
    
    item1 = queue._queue.get()
    item2 = queue._queue.get()
    assert item1[2].name == "high"
    assert item2[2].name == "low"

def test_04_engineer_context_pure_py():
    from services.engineer_context import EngineerContext
    
    md_text = "Here is the code:\n```python\ndef test():\n    pass\n```\nDone."
    code = EngineerContext.extract_pure_py(md_text)
    assert "def test():" in code
    
    raw_py = "import os\n\ndef main():\n    print('hello')"
    assert "import os" in EngineerContext.extract_pure_py(raw_py)
