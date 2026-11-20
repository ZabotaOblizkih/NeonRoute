"""
Consolidated End-to-End Smoke Test Suite for NeonRoute Engineer Subagent & Tool Pipeline.
Covers:
1. CodeStreamExtractor: micro-chunking stress test (3-5 chars) and tag boundary leakage prevention.
2. SubagentEngineer: full lifecycle (Architect -> Engineer -> write_file -> run_script -> self-correction).
3. SubagentEngineer with Thinking Tags (<think> isolation, streaming thought events).
4. ToolEngine lifecycle (write_file, check_artifact_substance, replace_symbol, patch_file, get_ast_skeleton, run_script, dispatch).
5. Surgical PatchApplier (SEARCH/REPLACE indentation & whitespace preservation).
6. Zero-Token Healing (AutoImporter NameError standard import injection).
7. ASTSkeletonizer (>600 lines signature preservation).
8. CodeValidator syntax and entry point guards.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

import config
try:
    assert_diag
except NameError:
    import sys
    _smoke_dir = str(Path(__file__).resolve().parent)
    if _smoke_dir not in sys.path:
        sys.path.insert(0, _smoke_dir)
    from conftest import assert_diag, assert_diag_eq, assert_diag_in
from services.subagent_engineer import SubagentEngineer, CodeStreamExtractor, extract_clean_code
from services.sandbox_manager import SandboxManager
from services.tool_engine import ToolEngine
from services.tool_parser import ToolCall, ToolParser
from services.patch_engine import PatchApplier
from services.code_engine import AutoImporter, ASTSkeletonizer
from services.code_validator import CodeValidator


# =========================================================================
# СЦЕНАРИЙ 1: ПОТОКОВЫЙ ПАРСЕР КОДА И СТРЕСС-ТЕСТ ЧАНКИНГА
# =========================================================================

def test_code_stream_extractor_fine_chunking_stress(smoke_isolation_guard):
    """
    Стресс-тест потокового парсера кода CodeStreamExtractor:
    - нарезка чанками по 3-4 символа;
    - извлечение чистого кода без тегов <<TOOL:write_file>>;
    - закрывающий тег <</TOOL:write_file>> ни при каких условиях не должен утечь в коллбэк редактора.
    """
    scen = "Сценарий 1: CodeStreamExtractor микро-чанкинг"
    received_chunks = []

    def on_chunk(chunk: str):
        received_chunks.append(chunk)

    extractor = CodeStreamExtractor(on_code_chunk_callback=on_chunk)
    raw_response = (
        "Пояснение перед кодом.\n"
        '<<TOOL:write_file filename="game.py">>\n'
        "import pygame\n"
        "def run_game():\n"
        "    print('GAME_RUNNING')\n"
        "<</TOOL:write_file>>\n"
        "Пояснение после кода."
    )

    chunk_size = 4
    for i in range(0, len(raw_response), chunk_size):
        extractor.feed(raw_response[i:i + chunk_size])

    clean_code = extractor.finalize()
    full_streamed = "".join(received_chunks)

    assert_diag("import pygame" in clean_code, scen, "Извлеченный код", "Содержит import pygame", clean_code)
    assert_diag("GAME_RUNNING" in clean_code, scen, "Тело функции", "Содержит GAME_RUNNING", clean_code)
    assert_diag("<<TOOL" not in clean_code, scen, "Очистка тега открытия", "Нет <<TOOL", clean_code)
    assert_diag("<</TOOL" not in clean_code, scen, "Очистка тега закрытия", "Нет <</TOOL", clean_code)

    # Проверка коллбэка редактора
    assert_diag("import pygame" in full_streamed, scen, "Стриминг в редактор", "Код передан в коллбэк", full_streamed)
    assert_diag("<</TOOL" not in full_streamed, scen, "Утечка закрывающего тега", "Нет <</TOOL в стриме", full_streamed)
    assert_diag("write_file" not in full_streamed, scen, "Утечка имени инструмента", "Нет write_file в стриме", full_streamed)

    # Проверка extract_clean_code на markdown-обертках
    cleaned_md = extract_clean_code("```python\nx = 10\ny = 20\n```")
    assert_diag(cleaned_md.strip() == "x = 10\ny = 20", scen, "Очистка markdown", "x = 10\\ny = 20", cleaned_md.strip())


# =========================================================================
# СЦЕНАРИЙ 2: ПОЛНЫЙ СКВОЗНОЙ ЦИКЛ СУБАГЕНТА И САМОКОРРЕКЦИЯ
# =========================================================================

def test_subagent_full_lifecycle_and_self_correction(smoke_isolation_guard):
    """
    Сквозной цикл Субагента:
    Архитектор (план) -> Инженер (SyntaxError) -> самокоррекция -> write_file -> run_script -> Успех.
    """
    scen = "Сценарий 2: Субагент полный цикл и самокоррекция"
    session_id = "smoke_eng_subagent_sess"
    SandboxManager.set_active_session_id(session_id)

    valid_code = (
        "import sys\n\n"
        "def main():\n"
        "    print('SUCCESS_ENGINEER_RUN')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
        "# " + ("A" * 360) + "\n"  # >350 байт для SubstanceGuard
    )

    class MockLMClient:
        def __init__(self):
            self.calls = 0

        def chat_completion(self, messages, *args, **kwargs):
            self.calls += 1
            chunk_cb = kwargs.get("chunk_callback")
            if self.calls == 1:
                # 1. Ответ Архитектора: валидный план
                ret = json.dumps({
                    "artifact_type": "script",
                    "intent": "implement",
                    "target_artifact": "main.py",
                    "verification_script": "main.py",
                    "plan": "Создать скрипт main.py и выполнить запуск"
                })
            elif self.calls == 2:
                # 2. Ход 1 Инженера: синтаксическая ошибка
                broken = "def main(\n    print('broken')\n# " + ("A" * 360)
                ret = f'<<TOOL:write_file filename="main.py">>\n{broken}\n<</TOOL:write_file>>'
            else:
                # 3. Ход 2 Инженера: самокоррекция и запуск
                ret = (
                    f'<<TOOL:write_file filename="main.py">>\n{valid_code}\n<</TOOL:write_file>>\n'
                    f'<<TOOL:run_script filename="main.py">><</TOOL:run_script>>'
                )
            if chunk_cb and callable(chunk_cb):
                chunk_cb(ret)
            return ret

    client = MockLMClient()
    res = SubagentEngineer.run_worker(
        task_prompt="Создай консольный скрипт main.py",
        context_hint="",
        lm_client=client,
        on_message_callback=lambda *a, **k: None,
        on_status_callback=lambda *a, **k: None
    )

    assert_diag(isinstance(res, dict), scen, "Результат субагента", "Словарь", type(res))
    assert_diag(res.get("success") is True, scen, "Флаг успеха", True, res.get("success"))
    assert_diag("SUCCESS_ENGINEER_RUN" in res.get("stdout", ""), scen, "Вывод скрипта", "SUCCESS_ENGINEER_RUN", res.get("stdout"))


# =========================================================================
# СЦЕНАРИЙ 3: СУБАГЕНТ НА THINKING-МОДЕЛИ С ИЗОЛЯЦИЕЙ РАССУЖДЕНИЙ
# =========================================================================

def test_subagent_with_thinking_model(smoke_isolation_guard):
    """
    Проверка сквозного цикла субагента при работе с thinking-моделью (<think>...</think> в потоке).
    """
    scen = "Сценарий 3: Субагент на thinking-модели"
    session_id = "smoke_eng_thinking_sess"
    SandboxManager.set_active_session_id(session_id)

    valid_code = (
        "import sys\n\n"
        "def main():\n"
        "    print('SUCCESS_THINKING_ENGINEER_RUN')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
        "# " + ("A" * 360) + "\n"
    )

    class MockThinkingLMClient:
        def __init__(self):
            self.calls = 0

        def chat_completion(self, messages, *args, **kwargs):
            self.calls += 1
            chunk_cb = kwargs.get("chunk_callback")
            if self.calls == 1:
                raw_plan = json.dumps({
                    "artifact_type": "script",
                    "intent": "implement",
                    "target_artifact": "think_main.py",
                    "verification_script": "think_main.py",
                    "plan": "Создать скрипт think_main.py и запустить"
                })
                ret = f"<think>\nАнализирую архитектуру...\n</think>\n```json\n{raw_plan}\n```"
            else:
                ret = (
                    "<think>\nПишу валидный код и запускаю скрипт...\n</think>\n"
                    f'<<TOOL:write_file filename="think_main.py">>\n{valid_code}\n<</TOOL:write_file>>\n'
                    f'<<TOOL:run_script filename="think_main.py">><</TOOL:run_script>>'
                )
            if chunk_cb and callable(chunk_cb):
                for token in ret.split(" "):
                    chunk_cb(token + " ")
            return ret

    client = MockThinkingLMClient()
    thought_events = []

    res = SubagentEngineer.run_worker(
        task_prompt="Создай консольный скрипт think_main.py",
        context_hint="",
        lm_client=client,
        model_id="deepseek-r1-distill-qwen-14b",
        on_thought_callback=lambda *a: thought_events.append(a),
        enable_thinking=True
    )

    assert_diag(res.get("success") is True, scen, "Флаг успеха с thinking", True, res.get("success"))
    assert_diag("SUCCESS_THINKING_ENGINEER_RUN" in res.get("stdout", ""), scen, "Вывод скрипта", "SUCCESS_THINKING_ENGINEER_RUN", res.get("stdout"))
    phases = [ev[0] for ev in thought_events if len(ev) == 3]
    stages = [ev[1] for ev in thought_events if len(ev) == 3]
    assert_diag("start" in stages and "chunk" in stages, scen, "События мыслей", "start и chunk в stages", stages)


# =========================================================================
# СЦЕНАРИЙ 4: ИНСТРУМЕНТЫ TOOL_ENGINE И СУБСТАНЦИЯ АРТЕФАКТА
# =========================================================================

def test_tool_engine_full_lifecycle(smoke_isolation_guard):
    """
    Полный жизненный цикл ToolEngine:
    write_file -> check_artifact_substance -> replace_symbol -> patch_file -> get_ast_skeleton -> run_script -> dispatch_tool_call.
    """
    scen = "Сценарий 4: Жизненный цикл ToolEngine"
    session_id = "smoke_tool_sess"
    ToolEngine.set_active_session_id(session_id)
    filename = "calculator_smoke.py"

    initial_code = (
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b\n\n"
        "class Calculator:\n"
        "    def compute(self, x: int) -> int:\n"
        "        return x * 10\n"
    )

    # 1. write_file
    res_write = ToolEngine.write_file(filename, initial_code)
    assert_diag(res_write.success is True, scen, "write_file status", True, res_write.success)
    target_path = Path(res_write.artifact_path)
    assert_diag(target_path.exists(), scen, "Файл на диске", True, target_path.exists())

    # 2. check_artifact_substance (>100 байт)
    is_solid, msg = ToolEngine.check_artifact_substance(target_path, min_bytes=100)
    assert_diag(is_solid is True, scen, "check_artifact_substance", True, is_solid)

    # 3. replace_symbol по AST
    new_multiply = (
        "def multiply(a: int, b: int) -> int:\n"
        "    # Обновленное умножение\n"
        "    return (a * b) + 1\n"
    )
    res_sym = ToolEngine.replace_symbol(filename, "multiply", new_multiply)
    assert_diag(res_sym.success is True, scen, "replace_symbol status", True, res_sym.success)
    assert_diag("Обновленное умножение" in target_path.read_text(encoding="utf-8"), scen, "replace_symbol content", "Обновленное умножение", target_path.read_text(encoding="utf-8"))

    # 4. patch_file (замена блока)
    res_patch = ToolEngine.patch_file(filename=filename, search_block="return x * 10", replace_block="return x * 20")
    assert_diag(res_patch.success is True, scen, "patch_file status", True, res_patch.success)
    assert_diag("return x * 20" in target_path.read_text(encoding="utf-8"), scen, "patch_file content", "return x * 20", target_path.read_text(encoding="utf-8"))

    # 5. get_ast_skeleton
    res_skel = ToolEngine.get_ast_skeleton(filename)
    assert_diag(res_skel.success is True, scen, "get_ast_skeleton status", True, res_skel.success)
    assert_diag("def add" in res_skel.output and "class Calculator" in res_skel.output, scen, "get_ast_skeleton output", "Сигнатуры сохранены", res_skel.output)

    # 6. run_script
    runner_name = "run_calculator_smoke.py"
    runner_code = (
        f"from {filename[:-3]} import Calculator, add, multiply\n"
        "c = Calculator()\n"
        "print(f'ADD:{add(2, 3)}')\n"
        "print(f'MUL:{multiply(2, 3)}')\n"
        "print(f'CALC:{c.compute(5)}')\n"
    )
    ToolEngine.write_file(runner_name, runner_code)
    res_run = ToolEngine.run_script(runner_name)
    assert_diag(res_run.exit_code == 0, scen, "run_script exit code", 0, res_run.exit_code)
    assert_diag("ADD:5" in res_run.stdout and "MUL:7" in res_run.stdout and "CALC:100" in res_run.stdout, scen, "run_script stdout", "ADD:5, MUL:7, CALC:100", res_run.stdout)

    # 7. execute_tool_call
    call_write = ToolCall(name="write_file", params={"filename": "dispatch_test.py"}, body="print('DISPATCH_OK')\n")
    dispatch_res = ToolEngine.execute_tool_call(call_write)
    assert_diag(dispatch_res.success is True, scen, "execute_tool_call write status", True, dispatch_res.success)

    call_exec = ToolCall(name="run_script", params={"filename": "dispatch_test.py"})
    res_exec = ToolEngine.execute_tool_call(call_exec)
    assert_diag(res_exec.exit_code == 0, scen, "execute_tool_call run exit code", 0, res_exec.exit_code)
    assert_diag("DISPATCH_OK" in res_exec.stdout, scen, "execute_tool_call run stdout", "DISPATCH_OK", res_exec.stdout)


# =========================================================================
# СЦЕНАРИЙ 5: ХИРУРГИЧЕСКИЙ ПАТЧЕР, АВТО-ИМПОРТЕР, СКЕЛЕТИЗАТОР И ВАЛИДАТОР
# =========================================================================

def test_engineer_patcher_auto_importer_skeletonizer_and_validator(smoke_isolation_guard):
    """
    Проверка хирургического патчера PatchApplier, Zero-Token Healing (AutoImporter),
    AST-скелетонизатора (ASTSkeletonizer) и валидатора синтаксиса (CodeValidator).
    """
    scen = "Сценарий 5: Утилиты кода инженера"

    # 1. Хирургический патчер SEARCH/REPLACE с сохранением отступов
    original = "class Engine:\n    def start(self):\n        return 'cold'\n"
    diff_text = (
        "<<<<<<< SEARCH\n"
        "    def start(self):\n"
        "        return 'cold'\n"
        "=======\n"
        "    def start(self):\n"
        "        return 'warm_ready'\n"
        ">>>>>>> REPLACE"
    )
    diff_res = PatchApplier.apply_diff_text(original, diff_text)
    assert_diag(diff_res.success is True, scen, "PatchApplier status", True, diff_res.success)
    assert_diag("warm_ready" in diff_res.new_content, scen, "PatchApplier content", "warm_ready", diff_res.new_content)

    # 2. Zero-Token Healing: авто-инъекция импорта по NameError в изолированном файле
    target_py = smoke_isolation_guard["data_dir"] / "heal_target.py"
    target_py.write_text("def area(r: float) -> float:\n    return math.pi * (r ** 2)\n", encoding="utf-8")
    injected = AutoImporter.try_inject_import(target_py, "math")
    assert_diag(injected is True, scen, "AutoImporter status", True, injected)
    assert_diag("import math" in target_py.read_text(encoding="utf-8"), scen, "AutoImporter file", "import math", target_py.read_text(encoding="utf-8"))

    # 3. ASTSkeletonizer: сохранение сигнатур классов и функций
    source = (
        "class DataProcessor:\n"
        "    def transform(self, items: list[int]) -> list[int]:\n"
        "        res = []\n"
        "        for x in items:\n"
        "            res.append(x * 2)\n"
        "        return res\n"
    )
    skeleton = ASTSkeletonizer.skeletonize(source)
    assert_diag("class DataProcessor" in skeleton, scen, "ASTSkeletonizer class", "class DataProcessor", skeleton)
    assert_diag("def transform" in skeleton, scen, "ASTSkeletonizer method", "def transform", skeleton)

    # 4. ToolParser: парсинг <<TOOL:...>> для write_file и replace_symbol
    llm_out = (
        '<<TOOL:write_file filename="app.py">>\nprint("ok")\n<</TOOL>>\n'
        '<<TOOL:replace_symbol filename="app.py" name="foo">>\ndef foo(): return 1\n<</TOOL>>'
    )
    parsed_calls = ToolParser.parse_tool_calls(llm_out)
    assert_diag(len(parsed_calls) == 2, scen, "ToolParser count", 2, len(parsed_calls))
    call_names = [c.name for c in parsed_calls]
    assert_diag("write_file" in call_names and "replace_symbol" in call_names, scen, "ToolParser names", "write_file and replace_symbol", call_names)

    # 5. CodeValidator: валидация синтаксиса и входных точек
    lib_code = "def calc(x: int) -> int:\n    return x * 2\n"
    assert_diag(CodeValidator.validate_python_syntax("module.py", lib_code, is_entry_file=False)[0] is True, scen, "Lib code validation", True, True)
    assert_diag(CodeValidator.validate_python_syntax("main.py", lib_code, is_entry_file=True)[0] is False, scen, "Entry code missing main check", False, False)

    entry_code = "def calc(x: int) -> int:\n    return x * 2\n\nif __name__ == '__main__':\n    print(calc(21))\n"
    assert_diag(CodeValidator.validate_python_syntax("main.py", entry_code, is_entry_file=True)[0] is True, scen, "Entry code valid", True, True)
    assert_diag(CodeValidator.validate_python_syntax("hack.py", "import subprocess\n", is_entry_file=False)[0] is False, scen, "Subprocess rejection", False, False)
