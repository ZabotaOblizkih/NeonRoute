from pathlib import Path

print("=== ПОИСК И АНАЛИЗ ASTSkeletonizer В services/ ===")
found = False
for p in Path("services").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "class ASTSkeletonizer" in txt or "def skeletonize" in txt:
        found = True
        print(f"\n[Файл: {p.as_posix()}]")
        lines = txt.splitlines()
        for idx, line in enumerate(lines):
            if "class ASTSkeletonizer" in line or "def skeletonize" in line:
                for j in range(max(0, idx - 2), min(len(lines), idx + 85)):
                    print(f"{j+1:4d} | {lines[j]}")
                print("-" * 60)

if not found:
    print("[-] Класс ASTSkeletonizer не найден в services/. Ищем по всему проекту...")
    for p in Path(".").glob("**/*.py"):
        if "venv" in p.parts or ".git" in p.parts:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if "class ASTSkeletonizer" in txt:
                print(f"[Найден в: {p.as_posix()}]")
        except Exception:
            pass
