from pathlib import Path

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/subagent_engineer.py (строки 380-435) ===")
for i in range(379, min(435, len(lines))):
    print(f"{i+1:3d} | {lines[i]}")
