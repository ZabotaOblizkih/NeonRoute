from pathlib import Path

print(f"{'Файл':<45} | {'Всего':<6} | {'Код':<6} | {'Пустых':<6} | {'Мусор %':<8}")
print("-" * 80)

for p in sorted(Path(".").glob("**/*.py")):
    # Пропускаем виртуальные окружения и кэши
    if any(part in p.parts for part in (".venv", "venv", ".git", "__pycache__", "build", "dist")):
        continue
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        continue
    if len(lines) >= 800:
        non_empty = [l for l in lines if l.strip()]
        empty_count = len(lines) - len(non_empty)
        ratio = empty_count / len(lines) if lines else 0
        print(f"{str(p):<45} | {len(lines):<6} | {len(non_empty):<6} | {empty_count:<6} | {ratio:<7.1%}")
