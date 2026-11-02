import sys
import ast
from pathlib import Path
import subprocess

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

engine_file = PROJECT_ROOT / "services" / "tool_engine.py"
raw_content = engine_file.read_text(encoding="utf-8")

# 1. Исправление бага: file_path: Path -> поддержка str и Path
old_substance_check = "if not file_path.is_file():"
new_substance_check = "file_path = Path(file_path)\n        if not file_path.is_file():"

if old_substance_check in raw_content:
    content_fixed = raw_content.replace(old_substance_check, new_substance_check, 1)
    print("[OK] Баг check_artifact_substance пропатчен (добавлено Path(file_path)).")
else:
    content_fixed = raw_content
    print("[INFO] Патч check_artifact_substance уже присутствует.")

# 2. Очистка паразитных пустых строк
raw_lines = content_fixed.splitlines()
clean_lines = []

for line in raw_lines:
    stripped = line.strip()
    if not stripped:
        continue
    if line.startswith("class ") or (line.startswith("def ") and not line.startswith("    ")):
        clean_lines.append("")
        clean_lines.append("")
    elif line.startswith("    def ") or line.startswith("    @"):
        clean_lines.append("")
    clean_lines.append(line)

final_code = "\n".join(clean_lines).strip() + "\n"

# 3. Проверка синтаксиса
try:
    ast.parse(final_code)
    print(f"[PASS] Синтаксис валиден! Новый размер: {len(final_code.splitlines())} строк (было {len(raw_lines)}).")
except SyntaxError as e:
    print(f"[FAIL] Синтаксическая ошибка: {e}")
    sys.exit(1)

# Перезаписываем файл
engine_file.write_text(final_code, encoding="utf-8")
print(f"[OK] services/tool_engine.py успешно очищен и сохранен.")

# 4. Прогоняем смок-тест
print("\n>>> Запуск смок-теста...")
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_tool_engine.py", "-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тест провален!")
    sys.exit(res.returncode)

print("[PASS] Все смок-тесты ToolEngine пройдены на 100%!")
