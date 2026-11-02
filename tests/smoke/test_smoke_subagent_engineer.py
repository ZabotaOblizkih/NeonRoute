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
            else:
                # 2. Ответ Инженера: генерация write_file и запуск
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
