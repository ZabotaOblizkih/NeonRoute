from pathlib import Path

print("=== 1. ТЕСТОВЫЕ ФАЙЛЫ В ПРОЕКТЕ ===")
test_files = list(Path("tests").glob("*.py"))
for tf in test_files:
    print(f"  {tf.name} ({tf.stat().st_size} bytes)")

print("\n=== 2. КАК ТЕСТИРУЮТСЯ UI/QT В TESTS (поиск QApplication, pytest-qt, conftest) ===")
for p in Path("tests").glob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if any(k in txt for k in ("QApplication", "qtbot", "qapp", "conftest", "pytest.fixture")):
        print(f"Найдены Qt-фикстуры в: {p.name}")
        for i, l in enumerate(txt.splitlines()[:30], 1):
            if any(k in l for k in ("QApplication", "qtbot", "fixture", "conftest")):
                print(f"    {i:2d}: {l.strip()[:90]}")

p_conf = Path("tests/conftest.py")
if p_conf.exists():
    print("\n=== 3. СОДЕРЖИМОЕ tests/conftest.py ===")
    print(p_conf.read_text(encoding="utf-8", errors="ignore")[:800])
