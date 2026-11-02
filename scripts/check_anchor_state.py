import sys
import json
import inspect
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

from services.roleplay_prompt_builder import RoleplayPromptBuilder

print("=" * 80)
print(" ПРОВЕРКА: ПОЧЕМУ СРАБАТЫВАЕТ СТАРЫЙ ДЕФОЛТНЫЙ ЯКОРЬ")
print("=" * 80)

# 1. Проверяем сессию
sess_file = project_root / "outputs" / "sessions" / "26e7d8f7-76db-429e-9f4e-58f3bfae3260" / "session.json"
if sess_file.exists():
    s_data = json.loads(sess_file.read_text(encoding="utf-8"))
    print(f"1. custom_depth_anchor в session.json: {repr(s_data.get('custom_depth_anchor'))}")
else:
    print("1. Файл сессии не найден.")

# 2. Проверяем наличие нового метода get_default_depth_anchor
has_new_default = hasattr(RoleplayPromptBuilder, "get_default_depth_anchor")
print(f"2. Метод get_default_depth_anchor в коде: {'ЕСТЬ' if has_new_default else 'НЕТ (код не обновлялся)'}")

# 3. Исходный код build_depth_anchor
print("\n3. Текущий исходный код build_depth_anchor:")
print("-" * 80)
print(inspect.getsource(RoleplayPromptBuilder.build_depth_anchor))
print("=" * 80)
