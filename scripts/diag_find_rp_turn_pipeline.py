from pathlib import Path

print("=== 1. ГДЕ СОЗДАЕТСЯ ЗАДАЧА 'LLM Ответ' ===")
for p in Path(".").rglob("*.py"):
    # Пропускаем виртуальные окружения и кэш
    if any(part in p.parts for part in (".venv", "venv", "__pycache__", "site-packages")):
        continue
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "LLM Ответ" in txt:
        print(f"Файл: {p}")
        for i, l in enumerate(txt.splitlines(), 1):
            if "LLM Ответ" in l or "RoleplayPromptBuilder" in l:
                print(f"  {i:4d}: {l.strip()[:100]}")

print("\n=== 2. ГДЕ В КОДЕ ВЫЗЫВАЕТСЯ RoleplayPromptBuilder.build ===")
for p in Path(".").rglob("*.py"):
    if any(part in p.parts for part in (".venv", "venv", "__pycache__", "site-packages")):
        continue
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "RoleplayPromptBuilder.build" in txt:
        print(f"Файл: {p}")
        for i, l in enumerate(txt.splitlines(), 1):
            if "RoleplayPromptBuilder.build" in l:
                print(f"  {i:4d}: {l.strip()[:100]}")
