import ast
from pathlib import Path

for path in [Path("ui/components/chat_bubble.py"), Path("ui/components/chat_message_widget.py")]:
    if path.exists():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        print(f"{path}: {classes}")
