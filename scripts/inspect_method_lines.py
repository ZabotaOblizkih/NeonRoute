from pathlib import Path

lines = Path("services/tool_engine.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 225–260 из services/tool_engine.py ---")
for idx in range(224, min(len(lines), 260)):
    print(f"{idx+1:4d}: {repr(lines[idx])}")
