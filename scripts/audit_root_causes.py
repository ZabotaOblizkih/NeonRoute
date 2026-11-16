from pathlib import Path

ROOT = Path(".")

def dump(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

def slice_lines(rel: str, start: int, end: int):
    p = ROOT / rel
    if not p.exists():
        print(f"[MISSING] {rel}")
        return
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    for i in range(max(1, start), min(len(lines), end) + 1):
        print(f"L{i:04d}: {lines[i-1]}")

def find_block(rel: str, needle: str, before: int = 5, after: int = 45):
    p = ROOT / rel
    if not p.exists():
        print(f"[MISSING] {rel}")
        return
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines, 1):
        if needle in line:
            print(f"--- Match '{needle}' at L{i} ---")
            slice_lines(rel, i - before, i + after)
            return
    print(f"[NOT FOUND] '{needle}' in {rel}")

# ПУНКТ 1: Скилл interactive_loop + normalize_artifact_format + планировщик
dump("1A. ENGINEER_SKILLS['interactive_loop'] (engineer_skill_router.py)")
find_block("services/engineer_skill_router.py", '"interactive_loop": {', before=1, after=65)

dump("1B. normalize_artifact_format (engineer_context.py)")
find_block("services/engineer_context.py", "def normalize_artifact_format", before=2, after=55)

dump("1C. plan_architecture (engineer_planner.py L53-L320)")
slice_lines("services/engineer_planner.py", 53, 320)

# ПУНКТ 2: MissingEntryPoint и откат/повторный запрос в SubagentEngineer
dump("2A. MissingEntryPoint (code_validator.py)")
find_block("services/code_validator.py", "MissingEntryPoint", before=15, after=30)

dump("2B. Валидация до записи и откат (subagent_engineer.py)")
find_block("services/subagent_engineer.py", "обнаружен невалидный синтаксис", before=20, after=55)

# ПУНКТ 3: check_artifact_substance и Smoke-тест GUI
dump("3A. check_artifact_substance (tool_engine.py)")
find_block("services/tool_engine.py", "def check_artifact_substance", before=2, after=55)

dump("3B. Smoke-тест GUI (sandbox_manager.py)")
find_block("services/sandbox_manager.py", "стресс-тест событий ввода", before=65, after=25)
