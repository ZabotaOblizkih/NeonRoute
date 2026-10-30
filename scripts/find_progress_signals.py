from pathlib import Path

for p in Path("ui").rglob("*.py"):
    try:
        content = p.read_text(encoding="utf-8")
        if "progress" in content.lower() and "pyqtsignal" in content.lower():
            for idx, line in enumerate(content.splitlines()):
                if "pyqtsignal" in line.lower() and "progress" in line.lower():
                    print(f"{p}:{idx+1} -> {line.strip()}")
    except Exception:
        pass
