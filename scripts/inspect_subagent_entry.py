from pathlib import Path

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 145–205 из services/subagent_engineer.py ---")
for idx in range(144, min(len(lines), 205)):
    print(f"{idx+1:4d}: {lines[idx]}")
