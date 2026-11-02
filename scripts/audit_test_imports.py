from pathlib import Path
import re

shim_names = [
    "services.config_manager",
    "services.engineer_controller",
    "services.lora_manager",
    "rpg_status_widget",
    "search_bar_widget",
    "ui.dialogs.models_manager_dialog"
]

print("[*] Сканирование папки tests/ на предмет импортов шимов:")
found_in_tests = 0
for test_file in Path("tests").glob("**/*.py"):
    txt = test_file.read_text(encoding="utf-8", errors="ignore")
    for s in shim_names:
        if s in txt:
            print(f"    [!] Найдено упоминание '{s}' в {test_file}")
            found_in_tests += 1

if found_in_tests == 0:
    print("[PASS] В папке tests/ нет ни одного импорта проверяемых шимов!")
