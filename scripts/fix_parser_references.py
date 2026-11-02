import sys
import subprocess
from pathlib import Path

parser_file = Path("services/engineer_parser.py")
content = parser_file.read_text(encoding="utf-8")

# Заменяем остаточные обращения к SubagentEngineer на канонические сервисы
content = content.replace("SubagentEngineer.BINARY_EXTENSIONS", "BINARY_EXTENSIONS")
content = content.replace("SubagentEngineer._extract_pure_py", "EngineerContext.extract_pure_py")
content = content.replace("SubagentEngineer._has_python_code", "EngineerContext.has_python_code")
content = content.replace("{last_error}", "{actions.last_error}")

parser_file.write_text(content, encoding="utf-8")
print(f"[OK] Ссылки в {parser_file} приведены к каноническим сервисам.")

# Проверяем AST
import ast
ast.parse(parser_file.read_text(encoding="utf-8"))
print("[PASS] AST services/engineer_parser.py валиден!")

# Запускаем смок-тесты
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_subagent_engineer.py", "-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res.returncode)

print("[PASS] Все смок-тесты пройдены на 100%!")
