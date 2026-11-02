from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
targets = ["Current Active Companions", "ActiveCast", "дочь работорговца"]

print("=" * 80)
print("ПОИСК ГЕНЕРАТОРОВ ДВОЙНОГО КАСТА И ГАЛЛЮЦИНАЦИЙ")
print("=" * 80)

for py_file in (ROOT / "services").glob("*.py"):
    txt = py_file.read_text(encoding="utf-8", errors="ignore")
    for t in targets:
        if t in txt:
            print(f"• Файл: {py_file.name} содержит маркер '{t}'")

# Проверим также data/cast или runtime файлы сессий
for f in (ROOT / "data").rglob("*"):
    if f.is_file() and f.suffix in [".json", ".db"]:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            if "дочь работорговца" in txt:
                print(f"• Найдена галлюцинация в файле данных: {f.relative_to(ROOT)}")
        except Exception:
            pass
print("=" * 80)
