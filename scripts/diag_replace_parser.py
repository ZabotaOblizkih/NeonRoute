import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

from services.tool_parser import ToolParser
from services.tool_engine import ToolEngine

print("=== 1. ПРОВЕРКА ToolEngine.parse_tool_calls ===")
print("Метод parse_tool_calls в ToolEngine:", hasattr(ToolEngine, "parse_tool_calls"))

test_variants = [
    # Вариант А: чистый тег
    '''
    ФАЗА 1: Удаление узла DummyAssistantHost
    <<TOOL:replace_symbol filename="test_assistant_flow.py" name="DummyAssistantHost">>
    DELETED
    <</TOOL:replace_symbol>>
    ''',
    # Вариант Б: тег внутри Markdown-блока
    '''
    ФАЗА 1: Диагноз
    ```python
    <<TOOL:replace_symbol filename="test_assistant_flow.py" name="DummyAssistantHost">>
    DELETED
    <</TOOL:replace_symbol>>
    ```
    ''',
    # Вариант В: только markdown с DELETED (как модель выдала на первом стенде)
    '''
    ФАЗА 1: Подтверждаю удаление DummyAssistantHost
    ФАЗА 2:
    ```python
    DELETED
    ```
    '''
]

for idx, txt in enumerate(test_variants, 1):
    print(f"\n--- Тест варианта {idx} ---")
    calls = ToolParser.parse_tool_calls(txt)
    print(f"ToolParser нашел вызовов: {len(calls)}")
    for c in calls:
        print(f"  -> Имя: {c.name}, параметры: {c.params}, body: {repr(c.body)}")

