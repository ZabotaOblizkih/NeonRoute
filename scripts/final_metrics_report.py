import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

print("=" * 80)
print(">>> 1. ФИНАЛЬНЫЙ СМОК-ТЕСТ СЬЮТ")
print("=" * 80)

smoke_tests = [
    "tests/smoke/test_smoke_tool_engine.py",
    "tests/smoke/test_smoke_subagent_engineer.py",
    "tests/smoke/test_smoke_assistant_6phases_pipeline.py",
    "tests/smoke/test_smoke_mainwindow_lifecycle.py",
    "tests/smoke/test_dialog_sync_smoke.py"
]

res = subprocess.run([sys.executable, "-m", "pytest"] + smoke_tests + ["-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res.returncode)

print("=" * 80)
print(">>> 2. СВОДНАЯ ТАБЛИЦА МЕТРИК РЕФАКТОРИНГА")
print("=" * 80)

metrics = [
    ("services/tool_engine.py", 3721, Path("services/tool_engine.py")),
    ("services/subagent_engineer.py", 2256, Path("services/subagent_engineer.py")),
    ("ui/models_manager_dialog.py", 2805, Path("ui/models_manager_dialog.py")),
    ("ui/dialogs/characters_gallery_dialog.py", 2054, Path("ui/dialogs/characters_gallery_dialog.py")),
    ("services/config_manager.py (шим)", 6, Path("services/config_manager.py")),
    ("services/engineer_controller.py (шим)", 7, Path("services/engineer_controller.py")),
    ("services/lora_manager.py (шим)", 11, Path("services/lora_manager.py")),
    ("ui/components/rpg_status_widget.py (шим)", 24, Path("ui/components/rpg_status_widget.py")),
    ("ui/components/search_bar_widget.py (шим)", 8, Path("ui/components/search_bar_widget.py")),
    ("ui/dialogs/models_manager_dialog.py (шим)", 22, Path("ui/dialogs/models_manager_dialog.py")),
]

print(f"{'Компонент':<44} | {'Было':<7} | {'Стало':<7} | {'Дельта':<8} | {'Статус':<15}")
print("-" * 92)

total_saved = 0
for name, before, path in metrics:
    if not path.exists():
        after = 0
        delta = f"-{before}"
        status = "Ликвидирован"
        total_saved += before
    else:
        after = len(path.read_text(encoding="utf-8").splitlines())
        diff = before - after
        delta = f"-{diff}" if diff > 0 else f"+{abs(diff)}"
        status = "Облегчен"
        total_saved += diff
    print(f"{name:<44} | {before:<7} | {after:<7} | {delta:<8} | {status:<15}")

print("-" * 92)
print(f"ИТОГО СОКРАЩЕНО ТЕХНИЧЕСКОГО ДОЛГА: {total_saved} строк кода и мусора!")
