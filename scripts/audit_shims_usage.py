import re
from pathlib import Path

shims = [
    "services/config_manager.py",
    "services/engineer_controller.py",
    "services/lora_manager.py",
    "ui/components/rpg_status_widget.py",
    "ui/components/search_bar_widget.py",
    "ui/dialogs/models_manager_dialog.py"
]

print("=" * 80)
print(">>> АУДИТ ЛЕГАСИ-ШИМОВ И ТОЧЕК ИХ ИМПОРТА")
print("=" * 80)

# 1. Читаем содержимое шимов
shim_modules = {}
for s in shims:
    p = Path(s)
    if p.exists():
        content = p.read_text(encoding="utf-8").strip()
        mod_name = s.replace("/", ".").replace("\\", ".")[:-3]
        shim_modules[mod_name] = (s, content)
        print(f"\n[ШИМ] {s} ({len(content.splitlines())} строк):")
        for line in content.splitlines():
            print(f"    | {line}")
    else:
        print(f"\n[WARN] Файл не найден: {s}")

# 2. Ищем все вхождения импортов в кодовой базе
print("\n" + "=" * 80)
print(">>> ПОИСК ТОЧЕК ИМПОРТА В ПРОЕКТЕ")
print("=" * 80)

found_usages = {m: [] for m in shim_modules}

for py_path in sorted(Path(".").glob("**/*.py")):
    # Исключаем сами шимы, venv, git, кэши
    if any(part in py_path.parts for part in (".git", ".venv", "venv", "__pycache__", "scripts")):
        continue
    posix_path = py_path.as_posix()
    if any(posix_path == s for s in shims):
        continue

    try:
        lines = py_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        continue

    for idx, line in enumerate(lines):
        for mod in shim_modules:
            # Ищем import mod или from mod import
            pattern = r'\b(from|import)\s+' + re.escape(mod) + r'\b'
            if re.search(pattern, line):
                found_usages[mod].append((posix_path, idx + 1, line.strip()))

for mod, usages in found_usages.items():
    shim_file, _ = shim_modules[mod]
    print(f"\n[*] Модуль: {mod} ({shim_file}) -> Использований: {len(usages)}")
    if usages:
        for fpath, lnum, code in usages:
            print(f"    • {fpath}:{lnum} -> {code}")
    else:
        print("    [0] Не используется ни в одном файле (мертвый код).")
