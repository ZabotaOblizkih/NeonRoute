from pathlib import Path
lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/subagent_engineer.py (строки 480-550: сборка messages) ===")
for j in range(479, min(len(lines), 550)):
    print(f"{j+1:4d} | {lines[j]}")
