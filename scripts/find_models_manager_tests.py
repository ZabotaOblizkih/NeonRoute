from pathlib import Path

print("[*] Поиск тестов, импортирующих ModelsManagerDialog или воркеры моделей:")
for p in Path("tests").glob("**/*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "ModelsManagerDialog" in txt or "ForgeScanWorker" in txt or "CivitaiParamsWorker" in txt:
            print(f"    {p}")
    except Exception:
        pass
