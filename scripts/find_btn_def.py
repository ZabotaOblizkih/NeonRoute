from pathlib import Path

for p in Path("ui").rglob("*.py"):
    try:
        content = p.read_text(encoding="utf-8")
        if "btn_translate_all" in content:
            for idx, line in enumerate(content.splitlines()):
                if "btn_translate_all" in line and ("=" in line or "QPushButton" in line or "PushButton" in line):
                    print(f"{p}:{idx+1} -> {line.strip()}")
    except Exception:
        pass
