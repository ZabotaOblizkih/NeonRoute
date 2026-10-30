import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  ПОИСК: МЕТОДЫ _append_message И session_controller")
print("=" * 75)

# 1. Ищем объявление append_message по всей папке ui/
print("\n[1] Поиск определения append_message во всех файлах ui/:")
for p in Path("ui").rglob("*.py"):
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        for idx, l in enumerate(lines):
            if "def _append_message" in l or "def append_message" in l:
                print(f"  • {p.relative_to(ROOT_DIR)}:{idx+1} -> {l.strip()}")
    except Exception:
        pass

# 2. Все методы в ui/controllers/session_controller.py
print("\n[2] Все методы класса в ui/controllers/session_controller.py:")
sc_path = Path("ui/controllers/session_controller.py")
if sc_path.exists():
    lines = sc_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        if l.strip().startswith("def "):
            print(f"  • Строка {idx+1}: {l.strip()}")

print("=" * 75)
