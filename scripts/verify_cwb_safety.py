import sys
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser
from services.context_window_builder import ContextWindowBuilder

card_path = ROOT / "characters" / "Кай и Эйла.json"
card = TavernParser.parse_file(str(card_path))

history = [
    {"role": "assistant", "content": "*Рынок рабов гудит...*"},
    {"role": "user", "content": "Я бросаю торговцу деньги"}
]

print("=" * 80)
print("ПРОВЕРКА: СБОРКА КОНТЕКСТА С ОТКЛЮЧЕННОЙ СКЛЕЙКОЙ В USER-СООБЩЕНИИ")
print("=" * 80)

# Эмулируем сборку
messages = ContextWindowBuilder.build_context_messages(
    character_card=card,
    history=history,
    mode="roleplay",
    user_name="Дэниэл",
    max_context_tokens=14000
)

print(f"Всего сообщений: {len(messages)}")
print(f"Системный промпт (messages[0]): {len(messages[0]['content'])} симв.")
print(f"Первый ход (messages[1]): {messages[1]['role']} -> {messages[1]['content'][:50]}...")
print(f"Второй ход (messages[2]): {messages[2]['role']} -> {repr(messages[2]['content'])}")
print("=" * 80)
