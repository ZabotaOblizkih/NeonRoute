import re
import ast
from pathlib import Path

# Читаем исходный вывод из нашего предыдущего запуска
# (или извлекаем внешний markdown-блок)
raw = Path("scripts/test_270_lines_stress.py").read_text(encoding="utf-8")

# Изолируем только внешний блок ```python ... ```
matches = re.findall(r'```python\s*([\s\S]*?)\s*```', raw)
# Берем самый длинный блок (весь файл целиком)
real_code = max(matches, key=len)

try:
    ast.parse(real_code)
    lines = real_code.splitlines()
    print("=" * 60)
    print("✅ РЕАЛЬНЫЙ СИНТАКСИС (AST): 100% ВАЛИДЕН!")
    print(f"📊 Объем: {len(lines)} строк")
    print(f"🔍 Наличие удаленного класса: {'TestSandboxTestFileExclusionFromGuiSmoke' in real_code}")
    print("=" * 60)
except SyntaxError as e:
    print(f"❌ Ошибка AST: {e}")
