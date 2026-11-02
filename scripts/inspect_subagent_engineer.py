import sys
import ast
from pathlib import Path

file_path = Path("services/subagent_engineer.py")
content = file_path.read_text(encoding="utf-8")
tree = ast.parse(content)

print(f"[*] Анализ файла: {file_path}")
print(f"[*] Всего строк: {len(content.splitlines())}")

# 1. Поиск классов и методов
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        bases = [b.id if isinstance(b, ast.Name) else ast.unparse(b) for b in node.bases]
        bases_str = f"({', '.join(bases)})" if bases else ""
        print(f"\nКласс: class {node.name}{bases_str} [строки {node.lineno}–{node.end_lineno}]")
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                first_line = ast.get_docstring(item)
                doc = (first_line.splitlines()[0] if first_line else "").strip()
                args = [a.arg for a in item.args.args]
                print(f"  • def {item.name:32s} ({', '.join(args[:4])}) -> {doc[:50]}")
    elif isinstance(node, ast.FunctionDef):
        print(f"\nФункция верхнего уровня: def {node.name}")

# 2. Проверка зависимостей от Qt
qt_imports = [line.strip() for line in content.splitlines() if "PyQt" in line or "QtCore" in line]
print(f"\n[*] Импорты Qt ({len(qt_imports)}):")
for qm in qt_imports[:5]:
    print(f"    {qm}")
