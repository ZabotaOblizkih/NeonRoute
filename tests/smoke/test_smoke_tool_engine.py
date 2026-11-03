import pytest
from pathlib import Path
from services.tool_engine import ToolEngine
from services.tool_parser import ToolCall

@pytest.fixture
def sandbox_session():
    """Изолированная сессия песочницы с гарантированной зачисткой файлов и состояния."""
    session_id = "smoke_tool_engine_fixture"
    ToolEngine.set_active_session_id(session_id)
    created_paths = []

    yield session_id, created_paths

    # 1. Зачистка временных файлов
    for p in created_paths:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    # 2. Сброс активной сессии для предотвращения утечки состояния
    ToolEngine.set_active_session_id(None)

def test_tool_engine_full_lifecycle(sandbox_session):
    session_id, created_paths = sandbox_session
    filename = "calculator_smoke.py"

    # 1. Запись файла (write_file)
    initial_code = (
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b\n\n"
        "class Calculator:\n"
        "    def compute(self, x: int) -> int:\n"
        "        return x * 10\n"
    )
    res_write = ToolEngine.write_file(filename, initial_code)
    assert res_write.success is True, f"write_file failed: {res_write.output}"
    target_path = Path(res_write.artifact_path)
    created_paths.append(target_path)
    assert target_path.exists(), "Файл не создан на диске!"

    # 2. Проверка содержательности (check_artifact_substance)
    is_solid, msg = ToolEngine.check_artifact_substance(target_path, min_bytes=100)
    assert is_solid is True, f"check_artifact_substance failed: {msg}"

    # 3. Замена символа по AST (replace_symbol)
    new_multiply = (
        "def multiply(a: int, b: int) -> int:\n"
        "    # Обновленное умножение\n"
        "    return (a * b) + 1\n"
    )
    res_sym = ToolEngine.replace_symbol(filename, "multiply", new_multiply)
    assert res_sym.success is True, f"replace_symbol failed: {res_sym.output}"
    assert "Обновленное умножение" in target_path.read_text(encoding="utf-8")

    # 4. Патчинг блока (patch_file)
    res_patch = ToolEngine.patch_file(
        filename=filename,
        search_block="return x * 10",
        replace_block="return x * 20"
    )
    assert res_patch.success is True, f"patch_file failed: {res_patch.output}"
    assert "return x * 20" in target_path.read_text(encoding="utf-8")

    # 5. Получение AST-скелета файла (get_ast_skeleton)
    res_skel = ToolEngine.get_ast_skeleton(filename)
    assert res_skel.success is True, f"get_ast_skeleton failed: {res_skel.output}"
    assert "def add" in res_skel.output
    assert "class Calculator" in res_skel.output

    # 6. Запуск скрипта в песочнице (run_script)
    runner_name = "run_calculator_smoke.py"
    runner_code = (
        f"from {filename[:-3]} import Calculator, add, multiply\n"
        "c = Calculator()\n"
        "print(f'ADD:{add(2, 3)}')\n"
        "print(f'MUL:{multiply(2, 3)}')\n"
        "print(f'CALC:{c.compute(5)}')\n"
    )
    res_runner_write = ToolEngine.write_file(runner_name, runner_code)
    created_paths.append(Path(res_runner_write.artifact_path))

    res_run = ToolEngine.run_script(runner_name)
    assert res_run.exit_code == 0, f"run_script failed: {res_run.stderr}"
    assert "ADD:5" in res_run.stdout
    assert "MUL:7" in res_run.stdout
    assert "CALC:100" in res_run.stdout

def test_tool_engine_dispatch_tool_call(sandbox_session):
    """Проверка диспетчеризации через типизированный ToolCall."""
    session_id, created_paths = sandbox_session
    filename = "dispatch_test.py"

    call_write = ToolCall(
        name="write_file",
        params={"filename": filename},
        body="print('DISPATCH_OK')\n"
    )
    res_write = ToolEngine.execute_tool_call(call_write)
    assert res_write.success is True, f"execute_tool_call write failed: {res_write.output}"
    if res_write.artifact_path:
        created_paths.append(Path(res_write.artifact_path))

    # Вызов run_script через ToolCall
    call_exec = ToolCall(
        name="run_script",
        params={"filename": filename}
    )
    res_exec = ToolEngine.execute_tool_call(call_exec)
    assert res_exec.exit_code == 0, f"execute_tool_call run failed: {res_exec.stderr}"
    assert "DISPATCH_OK" in res_exec.stdout
