from pathlib import Path

lines = Path("services/mode_coordinator.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 1–45 из services/mode_coordinator.py ---")
for idx in range(min(45, len(lines))):
    print(f"{idx+1:4d}: {lines[idx]}")
