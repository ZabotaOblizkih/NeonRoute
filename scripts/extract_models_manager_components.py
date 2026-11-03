import sys
import ast
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

mm_file = PROJECT_ROOT / "ui" / "models_manager_dialog.py"
mm_content = mm_file.read_text(encoding="utf-8")
mm_lines = mm_content.splitlines()

# 1. Создаем директорию ui/models_manager
mm_dir = PROJECT_ROOT / "ui" / "models_manager"
mm_dir.mkdir(parents=True, exist_ok=True)
(mm_dir / "__init__.py").write_text('"""Models Manager package."""\n', encoding="utf-8")

# 2. Находим границы виджетов (строки 53-348) и воркеров (строки 351-541)
w_start = None
w_end = None
wk_start = None
wk_end = None

for idx, l in enumerate(mm_lines):
    if l.startswith("class SamplingSlider("):
        w_start = idx
    if l.startswith("class RoutingTestWorker("):
        w_end = idx
        wk_start = idx
    if l.startswith("class ModelsManagerDialog("):
        wk_end = idx
        break

print(f"[*] Границы виджетов: строки {w_start+1}–{w_end} ({w_end - w_start} строк)")
print(f"[*] Границы воркеров: строки {wk_start+1}–{wk_end} ({wk_end - wk_start} строк)")

# Извлекаем импорты заголовка (первые 50 строк)
header_imports = "\n".join(mm_lines[:w_start])

# 3. Формируем ui/models_manager/widgets.py
widgets_lines = mm_lines[w_start:w_end]
widgets_code = f"""\"\"\"
ui/models_manager/widgets.py — Кастомные виджеты и элементы управления диалога менеджера моделей.
\"\"\"
{header_imports}

""" + "\n".join(widgets_lines) + "\n"

widgets_file = mm_dir / "widgets.py"
widgets_file.write_text(widgets_code, encoding="utf-8")
ast.parse(widgets_code)
print(f"[PASS] AST ui/models_manager/widgets.py валиден!")

# 4. Формируем ui/models_manager/workers.py
workers_lines = mm_lines[wk_start:wk_end]
workers_code = f"""\"\"\"
ui/models_manager/workers.py — Многопоточные воркеры сканирования, Civitai и FP8 конвертации.
\"\"\"
{header_imports}

""" + "\n".join(workers_lines) + "\n"

workers_file = mm_dir / "workers.py"
workers_file.write_text(workers_code, encoding="utf-8")
ast.parse(workers_code)
print(f"[PASS] AST ui/models_manager/workers.py валиден!")

# 5. Обновляем ui/models_manager_dialog.py
reexports = [
    "# === ВЫДЕЛЕННЫЕ ВИДЖЕТЫ И ЭЛЕМЕНТЫ УПРАВЛЕНИЯ ===",
    "from ui.models_manager.widgets import (",
    "    SamplingSlider,",
    "    SamplingDoubleSpinBox,",
    "    SamplingSpinBox,",
    "    FluentEditableComboBox,",
    "    FluentTextEdit,",
    "    RoutingResultBadge,",
    ")",
    "",
    "# === ВЫДЕЛЕННЫЕ ФОНОВЫЕ ВОРКЕРЫ QThread ===",
    "from ui.models_manager.workers import (",
    "    RoutingTestWorker,",
    "    ForgeScanWorker,",
    "    CivitaiParamsWorker,",
    "    FP8ConvertWorker,",
    ")",
    ""
]

new_mm_lines = mm_lines[:w_start] + reexports + mm_lines[wk_end:]
new_mm_code = "\n".join(new_mm_lines) + "\n"

# Проверяем AST обновленного файла
ast.parse(new_mm_code)
mm_file.write_text(new_mm_code, encoding="utf-8")
print(f"[OK] ui/models_manager_dialog.py успешно облегчен на {wk_end - w_start} строк!")

# 6. Запускаем сьют из 31 теста диалога
print("\n>>> Запуск сьюта 31 теста ModelsManagerDialog...")
res = subprocess.run([
    sys.executable, "-m", "pytest",
    "tests/test_models_manager_dialog.py",
    "tests/test_model_checkpoint_sync.py",
    "tests/smoke/test_dialog_sync_smoke.py",
    "-v"
], capture_output=True, text=True)

print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Тесты провалены!")
    sys.exit(res.returncode)

print("[PASS] ВСЕ 31 ТЕСТ ПРОЙДЕНЫ НА 100%!")
