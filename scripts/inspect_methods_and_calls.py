import re
from pathlib import Path

# 1. Вызов в services/subagent_engineer.py
sub_text = Path("services/subagent_engineer.py").read_text(encoding="utf-8")
for idx, line in enumerate(sub_text.splitlines()):
    if "check_artifact_substance" in line:
        print(f"[*] Вызов в subagent_engineer.py (строка {idx+1}):\n    {line.strip()}\n")

# 2. Список всех методов в ToolEngine
te_lines = Path("services/tool_engine.py").read_text(encoding="utf-8").splitlines()
print("[*] Методы класса ToolEngine:")
for idx, line in enumerate(te_lines):
    m = re.match(r"^\s{4}def\s+([a-zA-Z0-9_]+)\s*\((.*?)\)", line)
    if m:
        method_name = m.group(1)
        params = m.group(2).split(":")[0] if m.group(2) else ""
        print(f"    Строка {idx+1:4d}: def {method_name}({params.strip()})")
