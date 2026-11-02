from pathlib import Path

lines = Path("tests/test_subagent_engineer_loop.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 195–230 из test_subagent_engineer_loop.py ---")
for idx in range(194, min(len(lines), 230)):
    print(f"{idx+1:4d}: {lines[idx]}")
