import sys
import ast
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

sub_file = PROJECT_ROOT / "services" / "subagent_engineer.py"
sub_lines = sub_file.read_text(encoding="utf-8").splitlines()

# 1. Находим точные границы оригинального парсера в subagent_engineer.py
start_idx = None
end_idx = None

for idx, line in enumerate(sub_lines):
    if "# 1. Парсинг через теги write_file" in line:
        start_idx = idx
    if start_idx is not None and "write_blocks = clean_blocks" in line:
        end_idx = idx + 1
        break

if start_idx is None or end_idx is None:
    print(f"[FAIL] Границы не найдены (start={start_idx}, end={end_idx})")
    sys.exit(1)

print(f"[*] Вырезаем оригинальные строки {start_idx+1}–{end_idx} ({end_idx - start_idx} строк)")
extracted_lines = sub_lines[start_idx:end_idx]

# 2. Адаптируем под класс EngineerResponseParser:
# В оригинале отступ 12 пробелов. Сдвигаем на 8 пробелов для метода @staticmethod parse_turn_actions
adjusted_body = []
for line in extracted_lines:
    if line.startswith("            "):
        adjusted_body.append("        " + line[12:])
    elif not line.strip():
        adjusted_body.append("")
    else:
        adjusted_body.append("        " + line.lstrip())

# Заменяем обращения к локальным переменным на actions.*
final_body_lines = [
    "        actions = ParsedTurnActions()",
    "        response_text = str(response_text or \"\")",
    ""
]

for line in adjusted_body:
    # Заменяем write_blocks.append на actions.write_blocks.append
    l = line.replace("write_blocks.append(", "actions.write_blocks.append(")
    l = l.replace("patch_blocks.append(", "actions.patch_blocks.append(")
    l = l.replace("replace_blocks.append(", "actions.replace_blocks.append(")
    l = l.replace("git_checkpoint_results.append(", "actions.git_checkpoint_results.append(")
    l = l.replace("not write_blocks and not patch_blocks and not replace_blocks", "not actions.write_blocks and not actions.patch_blocks and not actions.replace_blocks")
    l = l.replace("not write_blocks", "not actions.write_blocks")
    l = l.replace("len(write_blocks)", "len(actions.write_blocks)")
    l = l.replace("for fn, cnt in write_blocks:", "for fn, cnt in actions.write_blocks:")
    l = l.replace("actions.patch_blocks = [(fn, body) for fn, body in patch_blocks", "actions.patch_blocks = [(fn, body) for fn, body in actions.patch_blocks")
    l = l.replace("p_before = len(patch_blocks)", "p_before = len(actions.patch_blocks)")
    l = l.replace("last_error = res.output", "actions.last_error = res.output")
    l = l.replace("actions.last_error = actions.last_error", "actions.last_error = res.output")
    final_body_lines.append(l)

final_body_lines.append("        actions.write_blocks = clean_blocks")
final_body_lines.append("        return actions")

parser_header = """\"\"\"
engineer_parser.py — Парсинг ответов LLM, извлечение блоков кода, тегов инструментов и fallback-эвристики.
\"\"\"
import re
import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from services.tool_engine import ToolEngine
from services.code_validator import CodeValidator
from services.engineer_context import EngineerContext, BINARY_EXTENSIONS

logger = logging.getLogger("NeonRoute.EngineerParser")

@dataclass
class ParsedTurnActions:
    \"\"\"Результат парсинга действий хода инженера.\"\"\"
    write_blocks: List[Tuple[str, str]] = field(default_factory=list)
    patch_blocks: List[Tuple[str, str]] = field(default_factory=list)
    replace_blocks: List[str] = field(default_factory=list)
    git_checkpoint_results: List[str] = field(default_factory=list)
    last_error: str = ""

    def has_any_actions(self) -> bool:
        return bool(self.write_blocks or self.patch_blocks or self.replace_blocks)

class EngineerResponseParser:
    \"\"\"Парсер ответов LLM с поддержкой явных тегов и многоуровневых fallback-эвристик.\"\"\"

    @staticmethod
    def parse_turn_actions(
        response_text: str,
        ws_dir: Path,
        plan: Any,
        task_prompt: str,
        context_hint: str,
        target_artifact: Optional[str],
        target_entry_file: Optional[str],
        script_to_run: Optional[str],
        turn: int,
        turn1_test_signature_mismatch: bool,
        verification_script: Optional[str],
        declared_entry_file: Optional[str],
        is_maintenance: bool,
        planned_files: List[str],
        file_backups: Dict[str, str]
    ) -> ParsedTurnActions:
"""

parser_content = parser_header + "\n".join(final_body_lines) + "\n"
parser_file = PROJECT_ROOT / "services" / "engineer_parser.py"
parser_file.write_text(parser_content, encoding="utf-8")
print(f"[OK] Сгенерирован services/engineer_parser.py")

# Проверка синтаксиса AST
try:
    ast.parse(parser_content)
    print("[PASS] AST services/engineer_parser.py валиден на 100%!")
except SyntaxError as e:
    print(f"[FAIL] Синтаксическая ошибка в engineer_parser.py: {e}")
    sys.exit(1)

# 3. Заменяем блок в services/subagent_engineer.py
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

# Добавляем импорт в заголовок subagent_engineer.py
for idx, l in enumerate(sub_lines):
    if l.startswith("from services.engineer_review import"):
        sub_lines.insert(idx + 1, "from services.engineer_parser import EngineerResponseParser, ParsedTurnActions")
        # Корректируем смещение индексов
        start_idx += 1
        end_idx += 1
        break

new_sub_lines = sub_lines[:start_idx] + replacement + sub_lines[end_idx:]
sub_file.write_text("\n".join(new_sub_lines) + "\n", encoding="utf-8")
print(f"[OK] services/subagent_engineer.py обновлен: вырезано {end_idx - start_idx} строк парсера.")

# Проверяем AST subagent_engineer.py
ast.parse(sub_file.read_text(encoding="utf-8"))
print("[PASS] AST services/subagent_engineer.py валиден!")

# 4. Прогоняем смок-тесты
print("\n>>> Запуск смок-тестов...")
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_subagent_engineer.py", "-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res.returncode)

print("[PASS] Все смок-тесты успешно пройдены!")
