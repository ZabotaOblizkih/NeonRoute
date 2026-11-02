import sys
import subprocess
from pathlib import Path

review_file = Path("services/engineer_review.py")
content = review_file.read_text(encoding="utf-8")

# Заменяем сломанную строку с кавычками на синтаксически корректную регулярку
broken_line = 'm_msg = re.search(r"[\'"]([^\'"]+)[\'"]", task_prompt)'
fixed_line = "m_msg = re.search(r'''['\"]([^'\"]+)['\"]''', task_prompt)"

if broken_line in content:
    content = content.replace(broken_line, fixed_line)
else:
    # Заменяем через поиск шаблона
    import re
    content = re.sub(r'm_msg = re\.search\(.*task_prompt\)', fixed_line, content)

review_file.write_text(content, encoding="utf-8")
print(f"[OK] {review_file} исправлен.")

# Проверяем AST
import ast
ast.parse(review_file.read_text(encoding="utf-8"))
print("[PASS] AST services/engineer_review.py валиден!")

# Запускаем смок-тесты
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_subagent_engineer.py", "-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res.returncode)

print("[PASS] Все 3 смок-теста успешно пройдены!")
