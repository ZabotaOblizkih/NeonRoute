import re
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")

def inspect_file(rel_path, patterns):
    p = ROOT / rel_path
    if not p.exists():
        print(f"❌ Файл {rel_path} не найден.")
        return
    print(f"\n{'='*30} {rel_path} {'='*30}")
    content = p.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        for pat in patterns:
            if re.search(pat, line, re.IGNORECASE):
                start = max(0, idx - 4)
                end = min(len(lines), idx + 12)
                print(f"\n--- [Строка {idx+1}] Совпадение по '{pat}': ---")
                for i in range(start, end):
                    prefix = "👉" if i == idx else "  "
                    print(f"{prefix} L{i+1}: {lines[i]}")
                break

# 1. Как RoleplayPromptBuilder собирает системный промпт и mes_example
inspect_file("services/roleplay_prompt_builder.py", [
    r"def build_system_prompt",
    r"mes_example",
    r"non-godmoding",
    r"def build_depth_anchor"
])

# 2. Как RoleplayFlowController инжектирует якорь в сообщения
inspect_file("services/roleplay_flow_controller.py", [
    r"depth_anchor",
    r"\[RPFlow\.DepthAnchor\]",
    r"prepare_messages",
    r"append"
])

# 3. Где в config.json или коде задаются дефолты temp=1.0 для roleplay
inspect_file("services/session_manager.py", [
    r"temp",
    r"roleplay",
    r"config\.json"
])
