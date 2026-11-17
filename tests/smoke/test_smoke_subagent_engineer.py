import json
import pytest
from services.subagent_engineer import SubagentEngineer, CodeStreamExtractor, extract_clean_code
from services.sandbox_manager import SandboxManager

@pytest.fixture
def clean_subagent_env():
    """Изолированная среда песочницы для теста субагента."""
    session_id = "smoke_subagent_fixture"
    SandboxManager.set_active_session_id(session_id)
    yield session_id
    SandboxManager.set_active_session_id(None)

def test_code_stream_extractor():
    """Проверка потокового парсера кода из стрима токенов LLM."""
    received_chunks = []
    def on_chunk(chunk: str):
        received_chunks.append(chunk)

    extractor = CodeStreamExtractor(on_code_chunk_callback=on_chunk)
    stream_tokens = ["Вот ", "код:\n", "```python\n", "def foo():\n", "    return 42\n", "```\n", "Удачи!"]
    for tok in stream_tokens:
        extractor.feed(tok)

    clean_code = extractor.finalize()
    full_streamed = "".join(received_chunks)

    assert "def foo():" in full_streamed
    assert "return 42" in full_streamed
    assert "```" not in full_streamed


def test_code_stream_extractor_with_tool_tags_fine_chunking():
    """Проверка потокового парсера кода из тегов <<TOOL:write_file>> с чанками по 3-5 символов."""
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

    # Разбиваем на мелкие чанки по 4 символа для стресс-теста буфера безопасного хвоста
    chunk_size = 4
    for i in range(0, len(raw_response), chunk_size):
        extractor.feed(raw_response[i:i + chunk_size])

    clean_code = extractor.finalize()
    full_streamed = "".join(received_chunks)

    # Проверяем извлеченный код
    assert "import pygame" in clean_code
    assert "def run_game():" in clean_code
    assert "GAME_RUNNING" in clean_code
    assert "<<TOOL" not in clean_code
    assert "<</TOOL" not in clean_code

    # Проверяем стриминг в коллбэк (в правое окно редактора): закрывающий тег не должен утечь
    assert "import pygame" in full_streamed
    assert "<</TOOL" not in full_streamed
    assert "write_file" not in full_streamed

def test_extract_clean_code():
    """Проверка очистки markdown-оберток из сгенерированного ответа."""
    raw = "```python\nx = 10\ny = 20\n```"
    cleaned = extract_clean_code(raw)
    assert cleaned.strip() == "x = 10\ny = 20"

def test_subagent_full_lifecycle_mock(clean_subagent_env):
    """Проверка полного сквозного цикла субагента: Архитектор -> Инженер -> write_file -> run_script -> Успех."""
    valid_code = (
        "import sys\n\n"
        "def main():\n"
        "    print('SUCCESS_ENGINEER_RUN')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
        "# " + ("A" * 360) + "\n"  # Гарантируем >350 байт для SubstanceGuard
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
                # 2. Ход 1 Инженера: намеренная синтаксическая ошибка (проверка ветки syntax_error_found и fingerprint)
                broken_syntax = "def main(\n    print('broken')\n# " + ("A" * 360)
                ret = f'<<TOOL:write_file filename="main.py">>\n{broken_syntax}\n<</TOOL:write_file>>'
            else:
                # 3. Ход 2 Инженера: самокоррекция после SyntaxError -> валидный write_file и запуск
                ret = (
                    f'<<TOOL:write_file filename="main.py">>\n{valid_code}\n<</TOOL:write_file>>\n'
                    f'<<TOOL:run_script filename="main.py">><</TOOL:run_script>>'
                )
            if chunk_cb and callable(chunk_cb):
                chunk_cb(ret)
            return ret

    client = MockLMClient()
    messages = []
    statuses = []

    res = SubagentEngineer.run_worker(
        task_prompt="Создай консольный скрипт main.py",
        context_hint="",
        lm_client=client,
        on_message_callback=lambda *a, **k: messages.append(a),
        on_status_callback=lambda *a, **k: statuses.append(a)
    )

    assert isinstance(res, dict)
    assert res.get("success") is True, f"Субагент завершился с ошибкой: {res}"
    assert "SUCCESS_ENGINEER_RUN" in res.get("stdout", "")


def test_subagent_full_lifecycle_with_thinking_tags(clean_subagent_env):
    """Проверка сквозного цикла субагента при работе с thinking-моделью (<think>...</think> в потоке)."""
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
                # Архитектор выдаёт рассуждения в <think>, затем JSON план
                raw_plan = json.dumps({
                    "artifact_type": "script",
                    "intent": "implement",
                    "target_artifact": "think_main.py",
                    "verification_script": "think_main.py",
                    "plan": "Создать скрипт think_main.py и запустить"
                })
                ret = f"<think>\nАнализирую задачу и архитектуру...\n</think>\n```json\n{raw_plan}\n```"
            else:
                # Ход Инженера: рассуждения в <think>, затем вызовы инструментов
                ret = (
                    "<think>\nПишу валидный код и запускаю скрипт...\n</think>\n"
                    f'<<TOOL:write_file filename="think_main.py">>\n{valid_code}\n<</TOOL:write_file>>\n'
                    f'<<TOOL:run_script filename="think_main.py">><</TOOL:run_script>>'
                )

            # Симулируем потоковую передачу токенов
            if chunk_cb and callable(chunk_cb):
                for token in ret.split(" "):
                    chunk_cb(token + " ")
            return ret

    client = MockThinkingLMClient()
    thought_events = []

    def on_thought(*args):
        thought_events.append(args)

    res = SubagentEngineer.run_worker(
        task_prompt="Создай консольный скрипт think_main.py",
        context_hint="",
        lm_client=client,
        model_id="deepseek-r1-distill-qwen-14b",
        on_thought_callback=on_thought,
        enable_thinking=True
    )

    assert isinstance(res, dict)
    assert res.get("success") is True, f"Субагент с thinking завершился с ошибкой: {res}"
    assert "SUCCESS_THINKING_ENGINEER_RUN" in res.get("stdout", "")

    # Проверяем, что коллбэк мыслей получил события (phase, stage, chunk)
    phases = [ev[0] for ev in thought_events if len(ev) == 3]
    stages = [ev[1] for ev in thought_events if len(ev) == 3]
    assert "start" in stages
    assert "chunk" in stages
    assert "architect" in phases or "worker" in phases

