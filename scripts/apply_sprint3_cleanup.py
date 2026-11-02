import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 1. Перенаправляем импорты на канонические модули
rewires = [
    (
        PROJECT_ROOT / "ui" / "controllers" / "dialog_controller.py",
        "from services.config_manager import ConfigManager",
        "from utils.config_manager import ConfigManager"
    ),
    (
        PROJECT_ROOT / "ui" / "dialogs" / "characters_gallery_dialog.py",
        "from services.lora_manager import LoraManager, LORA_REGISTRY_PATH",
        "from services.art_director.lora_manager import LoraManager, LORA_REGISTRY_PATH"
    ),
    (
        PROJECT_ROOT / "ui" / "dialogs" / "civitai_lora_dialog.py",
        "from services.lora_manager import LoraManager, DEFAULT_FORGE_LORA_DIR",
        "from services.art_director.lora_manager import LoraManager, DEFAULT_FORGE_LORA_DIR"
    ),
    (
        PROJECT_ROOT / "ui" / "dialogs" / "__init__.py",
        "from ui.dialogs.models_manager_dialog import ModelsManagerDialog",
        "from ui.models_manager_dialog import ModelsManagerDialog"
    )
]

for file_path, old_imp, new_imp in rewires:
    if file_path.exists():
        content = file_path.read_text(encoding="utf-8")
        if old_imp in content:
            content = content.replace(old_imp, new_imp)
            file_path.write_text(content, encoding="utf-8")
            print(f"[REWIRE] {file_path.relative_to(PROJECT_ROOT)}: обновлен импорт.")
        else:
            print(f"[SKIP] В {file_path.relative_to(PROJECT_ROOT)} импорт уже обновлен.")

# 2. Удаляем 6 файлов-шимов
shims_to_delete = [
    PROJECT_ROOT / "services" / "config_manager.py",
    PROJECT_ROOT / "services" / "engineer_controller.py",
    PROJECT_ROOT / "services" / "lora_manager.py",
    PROJECT_ROOT / "ui" / "components" / "rpg_status_widget.py",
    PROJECT_ROOT / "ui" / "components" / "search_bar_widget.py",
    PROJECT_ROOT / "ui" / "dialogs" / "models_manager_dialog.py"
]

for shim_path in shims_to_delete:
    if shim_path.exists():
        shim_path.unlink()
        print(f"[DELETED] Удален файл-шим: {shim_path.relative_to(PROJECT_ROOT)}")
    else:
        print(f"[ALREADY DELETED] {shim_path.relative_to(PROJECT_ROOT)}")

# 3. Верификация смок-тестов
print("\n>>> 1. Запуск смок-тестов...")
res_smoke = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_tool_engine.py", "tests/smoke/test_smoke_subagent_engineer.py", "-v"], capture_output=True, text=True)
print(res_smoke.stdout)
if res_smoke.returncode != 0:
    print(res_smoke.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res_smoke.returncode)

# 4. Верификация тестов разметки и инженера
print("\n>>> 2. Запуск тестов разметки и инженера...")
res_tests = subprocess.run([sys.executable, "-m", "pytest", "tests/test_rp_chat_markup.py", "tests/test_subagent_engineer_loop.py", "-v"], capture_output=True, text=True)
print(res_tests.stdout)
if res_tests.returncode != 0:
    print(res_tests.stderr)
    print("[FAIL] Тесты провалены!")
    sys.exit(res_tests.returncode)

print("\n[PASS] ВСЕ ШИМЫ ЛИКВИДИРОВАНЫ, ТЕСТЫ ЗЕЛЕНЫЕ (100% Pass Rate)!")
