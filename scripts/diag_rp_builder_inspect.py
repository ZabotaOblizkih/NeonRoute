import sys
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))

from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

print("=== 1. СИГНАТУРА И РЕАЛИЗАЦИЯ RoleplayPromptBuilder.build ===")
sig = inspect.signature(RoleplayPromptBuilder.build)
print(f"RoleplayPromptBuilder.build{sig}\n")

lines = inspect.getsourcelines(RoleplayPromptBuilder.build)[0]
for i, l in enumerate(lines[:60], 1):
    print(f"{i:2d}: {l.rstrip()}")

print("\n=== 2. РАЗБОР КАРТОЧКИ Isekai RPG.json ===")
card_path = Path("characters/Isekai RPG.json")
card = TavernParser.parse_file(str(card_path))
print(f"Имя: {card.name}")
print(f"Поля объекта card: {[k for k in dir(card) if not k.startswith('_')]}")
print(f"system_prompt (длина): {len(getattr(card, 'system_prompt', '') or '')}")
print(f"description (длина): {len(getattr(card, 'description', '') or '')}")
print(f"personality (длина): {len(getattr(card, 'personality', '') or '')}")
print(f"scenario (длина): {len(getattr(card, 'scenario', '') or '')}")
print(f"first_mes (длина): {len(getattr(card, 'first_mes', '') or '')}")

print("\n=== 3. ТЕСТОВЫЙ ВЫЗОВ RoleplayPromptBuilder.build ===")
try:
    # Пробуем вызвать build с карточкой и минимальной историей сообщений
    params = sig.parameters
    kwargs = {}
    if "card" in params:
        kwargs["card"] = card
    elif "character" in params:
        kwargs["character"] = card

    if "history" in params:
        kwargs["history"] = []
    elif "messages" in params:
        kwargs["messages"] = []

    res = RoleplayPromptBuilder.build(**kwargs) if kwargs else RoleplayPromptBuilder.build(card, [])
    
    if isinstance(res, list):
        print(f"Результат — список messages из {len(res)} элементов:")
        for idx, m in enumerate(res):
            role = m.get("role")
            content = m.get("content", "")
            print(f"\n--- [Message {idx}] Role: '{role}' | Длина: {len(content)} симв. ---")
            print(content[:500] + ("..." if len(content) > 500 else ""))
    elif isinstance(res, tuple):
        print(f"Результат — кортеж из {len(res)} элементов: {[type(x) for x in res]}")
    elif isinstance(res, str):
        print(f"Результат — строка ({len(res)} симв.):\n{res[:500]}...")
except Exception as e:
    print(f"Ошибка вызова RoleplayPromptBuilder.build: {e}")
