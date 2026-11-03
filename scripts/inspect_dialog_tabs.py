import ast
import re
from pathlib import Path

content = Path("ui/models_manager_dialog.py").read_text(encoding="utf-8")
tree = ast.parse(content)

dlg_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ModelsManagerDialog")
print(f"[*] Класс ModelsManagerDialog: строки {dlg_class.lineno}–{dlg_class.end_lineno} (всего {dlg_class.end_lineno - dlg_class.lineno} строк)")

print("\n[*] Ключевые методы построения интерфейса и вкладок:")
for item in dlg_class.body:
    if isinstance(item, ast.FunctionDef):
        name = item.name
        lines_count = item.end_lineno - item.lineno
        if any(k in name for k in ("tab", "ui", "build", "init", "worker", "save", "scan", "civitai", "router")):
            print(f"  • def {name:32s} [строки {item.lineno:4d}–{item.end_lineno:4d}] ({lines_count:4d} стр.)")
