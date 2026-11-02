import ast
from pathlib import Path

content = Path("services/subagent_engineer.py").read_text(encoding="utf-8")
tree = ast.parse(content)

subagent_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SubagentEngineer")
print(f"[*] Класс SubagentEngineer: строки {subagent_class.lineno}–{subagent_class.end_lineno} (всего {subagent_class.end_lineno - subagent_class.lineno} строк)")

print("\n[*] Все методы SubagentEngineer:")
for item in subagent_class.body:
    if isinstance(item, ast.FunctionDef):
        line_count = item.end_lineno - item.lineno
        # Проверяем вложенные функции внутри метода
        nested_defs = [n.name for n in ast.walk(item) if isinstance(n, ast.FunctionDef) and n is not item]
        nested_str = f" [вложенных функций: {len(nested_defs)} ({', '.join(nested_defs[:4])})]" if nested_defs else ""
        print(f"  • def {item.name:28s} строки {item.lineno:4d}–{item.end_lineno:4d} ({line_count:4d} строк){nested_str}")
