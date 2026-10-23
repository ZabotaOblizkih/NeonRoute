"""
Unit tests for robust Python code extraction and AST validation in SubagentEngineer.
"""

import ast
import logging
import pytest
from services.subagent_engineer import SubagentEngineer, _extract_pure_py


def test_extract_pure_py_empty():
    assert _extract_pure_py("") == ""
    assert _extract_pure_py("   \n\t  ") == ""
    assert SubagentEngineer._extract_pure_py(None) == ""


def test_extract_pure_py_closed_python_blocks():
    raw = """
Вот готовый скрипт:
```python
import os
import sys

def main():
    print("Hello from NeonRoute")

if __name__ == "__main__":
    main()
```
Надеюсь, это поможет!
"""
    expected = (
        'import os\n'
        'import sys\n\n'
        'def main():\n'
        '    print("Hello from NeonRoute")\n\n'
        'if __name__ == "__main__":\n'
        '    main()'
    )
    extracted = _extract_pure_py(raw)
    assert extracted == expected
    # AST parse must succeed
    ast.parse(extracted)


def test_extract_pure_py_multiple_blocks_selects_largest():
    raw = """
Сначала установите библиотеку:
```bash
pip install requests
```
А вот маленький сниппет:
```python
x = 1
```
А вот основной рабочий код:
```python
import requests

def fetch_data(url: str):
    response = requests.get(url)
    return response.json()
```
"""
    extracted = _extract_pure_py(raw)
    assert "def fetch_data" in extracted
    assert "pip install" not in extracted
    ast.parse(extracted)


def test_extract_pure_py_any_closed_block_without_lang():
    raw = """
Вот ваш скрипт:
```
def add(a, b):
    return a + b
```
"""
    extracted = _extract_pure_py(raw)
    assert extracted == "def add(a, b):\n    return a + b"
    ast.parse(extracted)


def test_extract_pure_py_unclosed_block():
    raw = """
Вот сгенерированный скрипт:
```python
import sys

def process_stream():
    for line in sys.stdin:
        print(line.strip())
"""
    extracted = _extract_pure_py(raw)
    assert extracted.startswith("import sys")
    assert "def process_stream():" in extracted
    ast.parse(extracted)


def test_extract_pure_py_unwrapped_with_intro_text():
    raw = """
Конечно! Ниже представлен код для решения вашей задачи на Python:
import math

def calculate_circle_area(radius):
    return math.pi * (radius ** 2)

if __name__ == '__main__':
    print(calculate_circle_area(5))
"""
    extracted = _extract_pure_py(raw)
    assert not extracted.startswith("Конечно!")
    assert extracted.startswith("import math")
    ast.parse(extracted)


def test_extract_pure_py_unwrapped_def_start():
    raw = """
Вот функция:
def greet(name: str) -> str:
    return f"Hello, {name}!"
"""
    extracted = _extract_pure_py(raw)
    assert extracted == 'def greet(name: str) -> str:\n    return f"Hello, {name}!"'
    ast.parse(extracted)


def test_ast_parse_syntax_error_handling(caplog):
    bad_code = "def broken_func(\n    x = "
    with caplog.at_level(logging.WARNING):
        try:
            ast.parse(bad_code)
        except SyntaxError as e:
            logging.getLogger("NeonRoute.SubagentEngineer").warning(
                f"[SubagentEngineer] Извлеченный код содержит синтаксическую ошибку: {e}"
            )

    assert any(
        "[SubagentEngineer] Извлеченный код содержит синтаксическую ошибку:" in r.message
        for r in caplog.records
    )


class MockLMClient:
    def __init__(self, response: str):
        self._response = response

    def chat_completion(self, *args, **kwargs):
        return self._response


def test_run_worker_syntax_error_returns_error_result(caplog):
    mock_client = MockLMClient(
        "Вот готовый скрипт:\n```python\ndef broken():\n    x = \n```"
    )
    with caplog.at_level(logging.WARNING):
        res = SubagentEngineer.run_worker(
            task_prompt="Напиши функцию broken",
            lm_client=mock_client
        )
    assert res["success"] is False
    assert "SyntaxError" in res["stderr"] or "синтаксическ" in res["summary"]
    assert any("[SubagentEngineer] Извлеченный код содержит синтаксическую ошибку:" in r.message for r in caplog.records)


def test_run_worker_unclosed_markdown_extracts_and_runs(tmp_path, monkeypatch):
    monkeypatch.setattr("services.sandbox_manager.SandboxManager.get_workspace_dir", lambda: tmp_path)
    mock_client = MockLMClient(
        "Вот скрипт:\n```python\nimport sys\n\ndef main():\n    print('NeonRoute OK')\n\nif __name__ == '__main__':\n    main()\n"
    )
    res = SubagentEngineer.run_worker(
        task_prompt="Напиши тестовый скрипт main.py",
        lm_client=mock_client
    )
    assert res["success"] is True
    assert (tmp_path / res["file_name"]).exists()


def test_extract_pure_py_prioritizes_modified_code_over_longer_original():
    orig_code = (
        "import sys\n"
        "# Very long original code\n"
        "def old_logic():\n"
        "    print('This is old logic that was long and untouched')\n"
        "    return 1 + 2 + 3 + 4 + 5\n"
    )
    short_modified = (
        "import sys\n"
        "def new_logic():\n"
        "    return 42\n"
    )
    raw_response = f"""
Вот старый код:
```python
{orig_code}
```
А вот обновленная версия с исправлениями:
```python
{short_modified}
```
"""
    # Without orig_content: selects longer block (old code)
    res_no_orig = _extract_pure_py(raw_response)
    assert res_no_orig == orig_code.strip()

    # With orig_content: prioritizes modified block even though it's shorter
    res_with_orig = _extract_pure_py(raw_response, orig_content=orig_code)
    assert res_with_orig == short_modified.strip()


def test_extract_pure_py_fallback_when_all_identical():
    code_a = "def foo(): pass"
    code_b = "def foo(): pass\n"
    raw_response = f"""
```python
{code_a}
```
```python
{code_b}
```
"""
    # When all blocks are identical to orig_content, fallback to max(key=len)
    res = _extract_pure_py(raw_response, orig_content="def foo(): pass")
    assert res == "def foo(): pass"


def test_extract_pure_py_multiple_diff_blocks_selects_largest_diff():
    orig_code = "def base(): pass"
    small_diff = "def minor(): pass"
    large_diff = (
        "def full_solution():\n"
        "    x = 100\n"
        "    y = 200\n"
        "    return x + y\n"
    )
    raw_response = f"""
```python
{orig_code}
```
```python
{small_diff}
```
```python
{large_diff}
```
"""
    res = _extract_pure_py(raw_response, orig_content=orig_code)
    assert res == large_diff.strip()


