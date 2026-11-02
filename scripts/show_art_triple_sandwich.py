import sys
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser
from services.session_manager import SessionManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder

card = TavernParser.parse_file(str(ROOT / "characters" / "Кай и Эйла.json"))
user_name = "Дэниэл"
user_text_with_art = "Нарисуй как я бросаю торговцу деньги"

history = [
    {"role": "assistant", "content": "*Рынок рабов гудит...*"},
    {"role": "user", "content": user_text_with_art}
]

# 1. Шаг ContextWindowBuilder
messages = SessionManager.get_lm_studio_messages(
    history=history,
    is_roleplay=True,
    character_card=card,
    user_name=user_name,
    history_limit=65
)

# 2. Шаг RoleplayFlowController: Арт-директива (L249-L264)
# Получаем директиву напрямую из билдера, как это делает контроллер
directive = RoleplayPromptBuilder.get_cinematic_scene_directive(scene_type="dialogue", target_model_id="realistic")

for idx in reversed(range(len(messages))):
    if messages[idx].get("role") == "user":
        messages[idx]["content"] = messages[idx]["content"] + "\n\n" + directive
        break

# 3. Шаг RoleplayFlowController: Depth-якорь (L271-L286)
depth_anchor = RoleplayPromptBuilder.build_depth_anchor(card, user_name=user_name, user_query=user_text_with_art)

for idx in reversed(range(len(messages))):
    if messages[idx].get("role") == "user":
        messages[idx]["content"] = messages[idx]["content"] + depth_anchor
        break

final_content = [m for m in messages if m["role"] == "user"][-1]["content"]

print("=" * 80)
print(" РЕАЛЬНЫЙ ТРОЙНОЙ 'БУТЕРБРОД' ПРИ АРТ-ТРИГГЕРЕ (role: 'user'):")
print("=" * 80)
print(final_content)
print("=" * 80)
