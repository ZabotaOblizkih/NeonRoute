import ast
import re
from pathlib import Path

ROOT = Path(".")

def dump_section(title: str):
    print("\n" + "=" * 80)
    print(f"AUDIT: {title}")
    print("=" * 80)

def read_text(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        print(f"[MISSING] {rel}")
        return ""
    return p.read_text(encoding="utf-8", errors="replace")

# 1. Профили и температура Инженера
dump_section("config.py (MODE_PROFILES / ENGINEER)")
cfg = read_text("config.py")
for m in re.finditer(r"(MODE_PROFILES\s*=.*?^\})", cfg, re.S | re.M):
    print(m.group(1)[:3000])

# 2. Промпты Архитектора и Инженера
dump_section("services/engineer_prompts.py")
print(read_text("services/engineer_prompts.py"))

# 3. Скилл interactive_loop и роутер
dump_section("services/engineer_skill_router.py (skills & interactive_loop)")
sr = read_text("services/engineer_skill_router.py")
for kw in ("interactive_loop", "SKILL", "prompt", "instruction"):
    pass
for idx, line in enumerate(sr.splitlines(), 1):
    if any(k in line.lower() for k in ("interactive_loop", "artifact_format", "pygame", "gui")):
        start = max(0, idx - 3)
        end = min(len(sr.splitlines()), idx + 25)
        print(f"--- lines {start+1}..{end} ---")
        print("\n".join(sr.splitlines()[start:end]))
        break
# Также проверим json-файлы скиллов, если они есть
for jf in sorted((ROOT / "data").glob("*skill*.json")) if (ROOT / "data").exists() else []:
    print(f"\n[JSON SKILL FILE]: {jf}")
    print(jf.read_text(encoding="utf-8", errors="replace")[:4000])

# 4. Логика EngineerPlanner (artifact_format, max_tokens, temp)
dump_section("services/engineer_planner.py (key parts)")
ep = read_text("services/engineer_planner.py")
for i, line in enumerate(ep.splitlines(), 1):
    if any(k in line for k in ("artifact_format", "max_tokens", "temperature", "interface_spec", "def plan")):
        print(f"L{i:04d}: {line}")

# 5. SubagentEngineer: SubstanceGuard, температура, ход 1/15 и откат
dump_section("services/subagent_engineer.py (SubstanceGuard, temp, retry)")
se = read_text("services/subagent_engineer.py")
for i, line in enumerate(se.splitlines(), 1):
    if any(k in line for k in ("SubstanceGuard", "_check_substance", "temperature", "temp=", "MissingEntryPoint", "План утверждён")):
        start = max(0, i - 3)
        end = min(len(se.splitlines()), i + 18)
        print(f"--- L{start+1}..{end} ---")
        print("\n".join(se.splitlines()[start:end]))

# 6. SandboxManager: Smoke-тест GUI
dump_section("services/sandbox_manager.py (GUI smoke test)")
sm = read_text("services/sandbox_manager.py")
for i, line in enumerate(sm.splitlines(), 1):
    if any(k in line.lower() for k in ("smoke", "gui", "pygame", "стресс-тест")):
        start = max(0, i - 2)
        end = min(len(sm.splitlines()), i + 25)
        print(f"--- L{start+1}..{end} ---")
        print("\n".join(sm.splitlines()[start:end]))
        break
