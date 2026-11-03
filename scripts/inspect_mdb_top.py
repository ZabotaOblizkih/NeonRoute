from pathlib import Path
lines = (Path(__file__).resolve().parent.parent / "services" / "memory_db.py").read_text(encoding="utf-8").splitlines()
for i in range(35, 90):
    print(f"{i+1}: {lines[i]}")
