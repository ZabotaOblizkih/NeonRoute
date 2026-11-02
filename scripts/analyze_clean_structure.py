import ast
import re
from pathlib import Path

content = Path("services/tool_engine.py").read_text(encoding="utf-8")

# Удаляем паразитные пустые строки между строками с одинаковым или вложенным отступом
raw_lines = content.splitlines()
clean_lines = []
for idx, line in enumerate(raw_lines):
    stripped = line.strip()
    if not stripped:
        # Оставляем пустую строку ТОЛЬКО если следующая непустая строка - это объявление def или class
        # или если мы между крупными блоками
        continue
    # Если объявление функции верхнего уровня или класса - добавляем перед ним пустую строку
    if line.startswith("class ") or (line.startswith("def ") and not line.startswith("    ")):
        clean_lines.append("")
        clean_lines.append("")
    elif line.startswith("    def ") or line.startswith("    @"):
        clean_lines.append("")
    clean_lines.append(line)

compact_code = "\n".join(clean_lines).strip() + "\n"

# Проверяем AST
tree = ast.parse(compact_code)
print(f"[*] Чистый объем кода после удаления CRLF-мусора: {len(compact_code.splitlines())} строк")
print("[PASS] AST дерево успешно собрано!")

# Выводим карту модулей/методов чистого кода
print("\n[*] Структура чистого ToolEngine:")
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        print(f"\nКласс: {node.name}")
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                doc = ast.get_docstring(item)
                first_doc = (doc.splitlines()[0] if doc else "нет описания").strip()
                print(f"  • def {item.name:25s} -> {first_doc}")
    elif isinstance(node, ast.FunctionDef) and node.name == "sanitize_code":
        print(f"\nФункция верхнего уровня: def sanitize_code")
