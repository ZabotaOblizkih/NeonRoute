import sys
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser
from services.context_window_builder import ContextWindowBuilder
from services.roleplay_prompt_builder import RoleplayPromptBuilder

card = TavernParser.parse_file(str(ROOT / "characters" / "Кай и Эйла.json"))
user_name = "Дэниэл"
raw_history = [
    {"role": "user", "content": "Я бросаю торговцу деньги"}
]

# 1. Пропускаем через ContextWindowBuilder (как делает реальный рантайм)
cwb_messages = ContextWindowBuilder.build_context_messages(
    character_card=card,
    history=raw_history,
    mode="roleplay",
    user_name=user_name,
    max_context_tokens=14000
)

last_user_msg = [m for m in cwb_messages if m["role"] == "user"][-1]

print("=" * 80)
print(" СОСТОЯНИЕ USER-СООБЩЕНИЯ ПОСЛЕ ContextWindowBuilder:")
print("=" * 80)
print(last_user_msg["content"])
print("=" * 80)
