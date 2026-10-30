from pathlib import Path
lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/subagent_engineer.py (строки 840-875) ===")
for j in range(839, min(len(lines), 875)):
    print(f"{j+1:4d} | {lines[j]}")
