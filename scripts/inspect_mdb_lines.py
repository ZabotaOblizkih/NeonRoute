from pathlib import Path
text = (Path(__file__).resolve().parent.parent / "services" / "memory_db.py").read_text(encoding="utf-8")
for i, line in enumerate(text.splitlines(), 1):
    if "LanceDB" in line or "Thread" in line or "memory_db =" in line or "def _init" in line:
        print(f"{i}: {line}")
