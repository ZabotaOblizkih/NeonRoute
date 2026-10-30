import sys
from pathlib import Path

# Корень проекта
sys.path.insert(0, str(Path(".").resolve()))

from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

print("=== 1. МЕТОДЫ TavernParser ===")
for m in dir(TavernParser):
    if not m.startswith("__"):
        print(f"  • {m}")

print("\n=== 2. МЕТОДЫ RoleplayPromptBuilder ===")
for m in dir(RoleplayPromptBuilder):
    if not m.startswith("__"):
        print(f"  • {m}")

print("\n=== 3. ПОИСК ВЫЗОВА RoleplayPromptBuilder В services/roleplay_flow_controller.py ===")
p_rfc = Path("services/roleplay_flow_controller.py")
if p_rfc.exists():
    lines = p_rfc.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines, 1):
        if "RoleplayPromptBuilder" in l or "system_prompt" in l or "SysPrompt" in l:
            print(f"  {i:4d}: {l.strip()[:100]}")
