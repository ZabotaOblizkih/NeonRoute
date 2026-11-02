import sys
import subprocess
from pathlib import Path

parser_file = Path("services/engineer_parser.py")
content = parser_file.read_text(encoding="utf-8")

# Устраняем self.* в статическом методе
content = content.replace("self.verification_script", "verification_script")
content = content.replace("self.artifact_format", 'getattr(plan, "artifact_format", None)')
content = content.replace("self.artifact_type", 'getattr(plan, "artifact_type", None)')

parser_file.write_text(content, encoding="utf-8")
print(f"[OK] {parser_file} избавлен от ссылок на self.*")

# 1. Проверяем смок-тесты
print("\n>>> 1. Запуск смок-тестов...")
res_smoke = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_subagent_engineer.py", "-v"], capture_output=True, text=True)
print(res_smoke.stdout)
if res_smoke.returncode != 0:
    print(res_smoke.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res_smoke.returncode)

# 2. Проверяем все 27 тестов инженера
print("\n>>> 2. Запуск основного набора 27 тестов...")
res_all = subprocess.run([sys.executable, "-m", "pytest", "tests/test_subagent_engineer_loop.py", "tests/test_engineer_refactoring_decomposition.py", "-v"], capture_output=True, text=True)
print(res_all.stdout)
if res_all.returncode != 0:
    print(res_all.stderr)
    print("[FAIL] Основные тесты провалены!")
    sys.exit(res_all.returncode)

print("\n[PASS] ВСЕ ТЕСТЫ ПРОЙДЕНЫ НА 100% (0 ошибок, 0 регрессий)!")
