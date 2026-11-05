import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

def test_01_sandbox_manager_paths(tmp_path):
    from services.sandbox_manager import SandboxManager
    import os
    
    # 1. Path Traversal
    assert SandboxManager.clean_workspace_filename("../../../../windows/system32/cmd.exe") == "windows/system32/cmd.exe"
    
    # 2. Изоляция директорий на чтение
    # Для этого теста мы просто проверяем, что системная директория блокируется
    try:
        SandboxManager.validate_read_path("C:/Windows/System32")
        assert False, "Должен был заблокировать системную папку"
    except PermissionError:
        pass

    # 3. Легальное чтение файла (создаем его в реальном tempdir операционной системы,
    # потому что tmp_path pytest'а может лежать в странных подпапках AppData)
    real_temp = Path(tempfile.gettempdir())
    dummy = real_temp / "neonroute_test_dummy.txt"
    dummy.write_text("hello", encoding="utf-8")
    try:
        # Патчим get_workspace_dir, чтобы песочница думала, что temp_dir - это и есть workspace
        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=real_temp):
            res = SandboxManager.validate_read_file_path(str(dummy))
            assert res.name == "neonroute_test_dummy.txt"
    finally:
        if dummy.exists():
            dummy.unlink()

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
    
    # 1. Извлечение из Markdown блока
    md_text = "Here is the code:\n```python\ndef test():\n    pass\n```\nDone."
    code = EngineerContext.extract_pure_py(md_text)
    assert "def test():" in code
    
    # 2. Если нет Markdown, но есть явный синтаксис (def/import)
    raw_py = "import os\n\ndef main():\n    print('hello')"
    assert "import os" in EngineerContext.extract_pure_py(raw_py)
