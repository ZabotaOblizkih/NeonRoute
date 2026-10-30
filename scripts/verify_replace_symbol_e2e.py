import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.git_sandbox import SandboxManager
from services.tool_parser import ToolParser
from services.tool_engine import ToolEngine

sample_code = """
class Calculator:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b
"""

# Создаем тестовый файл строго внутри песочницы проекта
ws_dir = SandboxManager.get_workspace_dir()
test_file = ws_dir / "_bench_calc_e2e.py"
test_file.write_text(sample_code.strip() + "\n", encoding="utf-8")

try:
    # 1. Проверка парсера тегов
    tag_input = f"""
    Подтверждаю правку метода subtract.
    <<TOOL:replace_symbol filename="{test_file.name}" name="subtract">>
        def subtract(self, a, b):
            # Новая реализация
            return int(a) - int(b)
    <</TOOL:replace_symbol>>
    """
    calls = ToolParser.parse_tool_calls(tag_input)
    assert len(calls) == 1, f"Ожидался 1 вызов, получено: {len(calls)}"
    tc = calls[0]
    assert tc.name == "replace_symbol"
    assert tc.params["symbol_name"] == "subtract"

    # 2. Проверка исполнения замены движком
    res = ToolEngine.replace_symbol(test_file.name, tc.params["symbol_name"], tc.body)
    assert res.success, f"replace_symbol завершился с ошибкой: {res.output}"

    updated_text = test_file.read_text(encoding="utf-8")
    assert "int(a) - int(b)" in updated_text
    assert "def add(self, a, b):" in updated_text

    # 3. Проверка удаления через DELETED
    del_res = ToolEngine.replace_symbol(test_file.name, "add", "DELETED")
    assert del_res.success, f"Удаление завершилось с ошибкой: {del_res.output}"
    final_text = test_file.read_text(encoding="utf-8")
    assert "def add" not in final_text
    assert "def subtract" in final_text

    print("✅ Сквозной тест replace_symbol (парсинг + замена + удаление) в песочнице УСПЕШНО ПРОЙДЕН!")
finally:
    if test_file.exists():
        test_file.unlink()
