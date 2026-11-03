from pathlib import Path

lines = Path("tests/test_mode_switch_debounce_and_splitter.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 1–55 из test_mode_switch_debounce_and_splitter.py ---")
for idx in range(min(55, len(lines))):
    print(f"{idx+1:4d}: {lines[idx]}")
