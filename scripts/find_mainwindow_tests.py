from pathlib import Path

print("[*] Поиск тестов, импортирующих MainWindow:")
for p in Path("tests").glob("**/*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "MainWindow" in txt:
            print(f"    {p}")
    except Exception:
        pass
