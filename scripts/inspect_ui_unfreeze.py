from pathlib import Path

lines = Path("tests/test_ui_unfreeze.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 1–40 из test_ui_unfreeze.py ---")
for idx in range(min(40, len(lines))):
    print(f"{idx+1:4d}: {lines[idx]}")
