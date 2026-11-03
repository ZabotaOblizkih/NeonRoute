import ast
from pathlib import Path

file_path = Path("ui/dialogs/characters_gallery_dialog.py")
content = file_path.read_text(encoding="utf-8")
tree = ast.parse(content)

print(f"[*] Анализ файла: {file_path}")
print(f"[*] Всего строк: {len(content.splitlines())}")

print("\n[*] Классы верхнего уровня:")
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        bases = [b.id if isinstance(b, ast.Name) else ast.unparse(b) for b in node.bases]
        bases_str = f"({', '.join(bases)})" if bases else ""
        lines_count = node.end_lineno - node.lineno
        print(f"  • class {node.name:32s} {bases_str:<25} [строки {node.lineno:4d}–{node.end_lineno:4d}] ({lines_count:4d} стр.)")
