import sys
import json
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser
from services.session_manager import SessionManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder

# 1. Загрузка карточки
card_path = ROOT / "characters" / "Кай и Эйла.json"
card = TavernParser.parse_file(str(card_path))
user_name = "Дэниэл"
user_text = "Я бросаю торговцу деньги"

history = [
    {"role": "assistant", "content": "*Рынок рабов гудит грубыми шутками...*"},
    {"role": "user", "content": user_text}
]

print("=" * 80)
print(" РЕКОНСТРУКЦИЯ РЕАЛЬНОГО Модели ВХОДА (КАК БЫЛО В РАНТАЙМЕ NEONROUTE)")
print("=" * 80)
print(f"1. ТО, ЧТО ТЫ НАПИСАЛ В ЧАТЕ:\n   \"{user_text}\"")
print("-" * 80)

# ШАГ 1: Что сделал SessionManager.get_lm_studio_messages (ContextWindowBuilder L920-932)
messages = SessionManager.get_lm_studio_messages(
    history=history,
    is_roleplay=True,
    character_card=card,
    user_name=user_name,
    history_limit=65
)

user_msg_step1 = [m for m in messages if m["role"] == "user"][-1]["content"]

# ШАГ 2: Что сделал RoleplayFlowController (L271-286: подклейка Depth-0 якоря)
depth_anchor = RoleplayPromptBuilder.build_depth_anchor(
    card,
    user_name=user_name,
    user_query=user_text,
    custom_anchor=None  # Дефолтный якорь сессии
)

for idx in reversed(range(len(messages))):
    if messages[idx].get("role") == "user":
        user_msg_copy = dict(messages[idx])
        _c = user_msg_copy.get("content")
        if isinstance(_c, str):
            user_msg_copy["content"] = _c + depth_anchor
        messages[idx] = user_msg_copy
        break

final_user_content = [m for m in messages if m["role"] == "user"][-1]["content"]

print("2. ТО, ЧТО ФИЗИЧЕСКИ ПОЛУЧИЛА МОДЕЛЬ В messages[-1] (role: 'user'):")
print("-" * 80)
print(final_user_content)
print("-" * 80)

print("\n3. ВИД В ТОКЕНИЗАТОРЕ СЕТИ (CHATML ФОРМАТ QWEN2.5):")
print("-" * 80)
chatml_block = f"<|im_start|>user\n{final_user_content}\n<|im_end|>\n<|im_start|>assistant\n"
print(chatml_block)
print("=" * 80)
