import sys
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

card = TavernParser.parse_file(str(ROOT / "characters" / "Кай и Эйла.json"))

sig = inspect.signature(RoleplayPromptBuilder.build)
print("=" * 80)
print(f"СИГНАТУРА RoleplayPromptBuilder.build:\n{sig}")
print("=" * 80)

# Вызываем с аргументами по умолчанию, передавая только то, что поддерживается
kwargs = {}
for param in sig.parameters.values():
    if param.name in ("character_card", "card"):
        kwargs[param.name] = card
    elif param.name in ("user_name", "user"):
        kwargs[param.name] = "Дэниэл"

sys_prompt = RoleplayPromptBuilder.build(**kwargs)

print(f"Промпт успешно собран! Длина: {len(sys_prompt)} симв.")
print("-" * 80)

if "### Примеры реплик:" in sys_prompt:
    print(" БЛОК '### Примеры реплик:' ПРИСУТСТВУЕТ В ПРОМПТЕ!")
    idx = sys_prompt.find("### Примеры реплик:")
    print(sys_prompt[idx:idx+450] + "\n...")
else:
    print("❌ Блок '### Примеры реплик:' ОТСУТСТВУЕТ.")
    # Проверим альтернативные маркеры
    for marker in ["mes_example", "примеры", "<start>", "example chat"]:
        if marker in sys_prompt.lower():
            print(f"  • Найдено по маркеру '{marker}'!")
print("=" * 80)
