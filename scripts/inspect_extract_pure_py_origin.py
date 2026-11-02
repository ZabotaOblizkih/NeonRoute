import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

from services.subagent_engineer import SubagentEngineer

print(f"[*] hasattr(SubagentEngineer, '_extract_pure_py'): {hasattr(SubagentEngineer, '_extract_pure_py')}")
print(f"[*] hasattr(SubagentEngineer, '_has_python_code'): {hasattr(SubagentEngineer, '_has_python_code')}")

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()
print("\n[*] Все вхождения _extract_pure_py в subagent_engineer.py:")
for idx, line in enumerate(lines):
    if "_extract_pure_py" in line:
        print(f"    Строка {idx+1:4d}: {line.strip()}")
