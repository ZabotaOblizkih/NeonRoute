from pathlib import Path
lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/subagent_engineer.py (строки 675-730: разбор tool_calls) ===")
for j in range(674, min(len(lines), 730)):
    print(f"{j+1:4d} | {lines[j]}")

print("\n=== services/subagent_engineer.py (строки 810-850: _extract_pure_py fallback) ===")
for j in range(809, min(len(lines), 850)):
    print(f"{j+1:4d} | {lines[j]}")
