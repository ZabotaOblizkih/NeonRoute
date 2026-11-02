import ast
from pathlib import Path

content = Path("services/subagent_engineer.py").read_text(encoding="utf-8")
tree = ast.parse(content)

print("[*] Импорты подсистем engineer в services/subagent_engineer.py:")
for node in tree.body:
    if isinstance(node, ast.ImportFrom) and node.module and "engineer" in node.module:
        names = [n.name for n in node.names]
        print(f"    from {node.module} import {', '.join(names)}")
    elif isinstance(node, ast.Import):
        for n in node.names:
            if "engineer" in n.name:
                print(f"    import {n.name}")
