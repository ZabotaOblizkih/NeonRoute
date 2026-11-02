import sys
import ast
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

parser_file = PROJECT_ROOT / "services" / "engineer_parser.py"

# 1. Проверяем синтаксис созданного engineer_parser.py
try:
    ast.parse(parser_file.read_text(encoding="utf-8"))
    print("[PASS] AST services/engineer_parser.py валиден на 100%!")
except SyntaxError as e:
    print(f"[FAIL] Синтаксическая ошибка в engineer_parser.py: {e}")
    sys.exit(1)

# 2. Подключаем в subagent_engineer.py
sub_file = PROJECT_ROOT / "services" / "subagent_engineer.py"
sub_lines = sub_file.read_text(encoding="utf-8").splitlines()

# Добавляем импорт EngineerResponseParser
for idx, l in enumerate(sub_lines):
    if l.startswith("from services.engineer_review import"):
        sub_lines.insert(idx + 1, "from services.engineer_parser import EngineerResponseParser, ParsedTurnActions")
        break

# Ищем начало блока (строка 520) и конец (строка 836)
start_idx = None
end_idx = None

for idx, line in enumerate(sub_lines):
    if "# 1. Парсинг через теги write_file" in line:
        start_idx = idx
    if start_idx is not None and "write_blocks = clean_blocks" in line:
        end_idx = idx + 1
        break

if start_idx is None or end_idx is None:
    print("[FAIL] Не удалось найти границы парсера в subagent_engineer.py!")
    sys.exit(1)

indent = "            "
replacement = [
    f"{indent}# === ПАРСИНГ ОТВЕТА МОДЕЛИ (Делегировано в EngineerResponseParser) ===",
    f"{indent}parsed_actions = EngineerResponseParser.parse_turn_actions(",
    f"{indent}    response_text=response_text,",
    f"{indent}    ws_dir=ws_dir,",
    f"{indent}    plan=plan,",
    f"{indent}    task_prompt=task_prompt,",
    f"{indent}    context_hint=context_hint,",
    f"{indent}    target_artifact=target_artifact,",
    f"{indent}    target_entry_file=target_entry_file,",
    f"{indent}    script_to_run=script_to_run,",
    f"{indent}    turn=turn,",
    f"{indent}    turn1_test_signature_mismatch=turn1_test_signature_mismatch,",
    f"{indent}    verification_script=self.verification_script,",
    f"{indent}    declared_entry_file=declared_entry_file,",
    f"{indent}    is_maintenance=is_maintenance,",
    f"{indent}    planned_files=planned_files,",
    f"{indent}    file_backups=file_backups",
    f"{indent})",
    f"{indent}write_blocks = parsed_actions.write_blocks",
    f"{indent}patch_blocks = parsed_actions.patch_blocks",
    f"{indent}replace_blocks = parsed_actions.replace_blocks",
    f"{indent}git_checkpoint_results = parsed_actions.git_checkpoint_results",
    f"{indent}if parsed_actions.last_error:",
    f"{indent}    last_error = parsed_actions.last_error",
    ""
]

sub_lines = sub_lines[:start_idx] + replacement + sub_lines[end_idx:]
sub_file.write_text("\n".join(sub_lines) + "\n", encoding="utf-8")
print(f"[OK] subagent_engineer.py облегчен: заменено {end_idx - start_idx} строк на чистый вызов.")

# 3. Проверяем AST subagent_engineer.py
ast.parse(sub_file.read_text(encoding="utf-8"))
print("[PASS] AST services/subagent_engineer.py валиден!")

# 4. Прогоняем смок-тесты
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_subagent_engineer.py", "-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res.returncode)

print("[PASS] Все смок-тесты успешно пройдены!")
