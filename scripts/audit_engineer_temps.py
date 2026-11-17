from pathlib import Path

for rel in ("services/subagent_engineer.py", "services/engineer_planner.py", "services/engineer_tdd.py"):
    print(f"\n=== {rel} ===")
    for i, line in enumerate(Path(rel).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if "temp" in line.lower() and ("worker_temp" in line or "temperature" in line or "current_temp" in line):
            print(f"L{i:04d}: {line}")

print("\n=== tests/ (mentions of worker_temp or temperature in engineer tests) ===")
for tf in sorted(Path("tests").rglob("*.py")):
    txt = tf.read_text(encoding="utf-8", errors="replace")
    if "SubagentEngineer" in txt and "temp" in txt.lower():
        print(f" - {tf}")
