from pathlib import Path

print("[*] Поиск тестов, импортирующих SubagentEngineer:")
for p in Path("tests").glob("**/*.py"):
    try:
        txt = p.read_text(encoding="utf-8")
        if "SubagentEngineer" in txt:
            print(f"    {p}")
    except Exception:
        pass
