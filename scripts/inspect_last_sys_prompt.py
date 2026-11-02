import sys
import json
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

from services.session_manager import SessionManager
from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.tavern_models import CharacterCard

sess_id = "26e7d8f7-76db-429e-9f4e-58f3bfae3260"
sess_dir = project_root / "outputs" / "sessions" / sess_id
sess_file = sess_dir / "session.json"

print("=" * 80)
print(f" ДИАГНОСТИКА СИСТЕМНОГО ПРОМПТА СЕССИИ: {sess_id}")
print("=" * 80)

if not sess_file.exists():
    print(f"❌ Файл сессии {sess_file} не найден.")
    sys.exit(1)

sess_data = json.loads(sess_file.read_text(encoding="utf-8"))

# 1. Проверяем наличие кастомного оверрайда в сессии
custom_sys = sess_data.get("custom_system_prompt")
print(f"• custom_system_prompt в сессии: {'ЗАДАН (кастомный)' if custom_sys else 'НЕТ (используется дефолтная сборка)'}")

# 2. Проверяем сообщения сессии на наличие системного блока
system_messages = [m for m in sess_data.get("messages", []) if m.get("role") == "system"]
if system_messages:
    print(f"• В истории сессии найдено сохраненных системных сообщений: {len(system_messages)}")

# 3. Загружаем карточку и собираем боевой промпт так, как его собирает контроллер
char_data = sess_data.get("character_data")
card = None
if char_data:
    try:
        card = CharacterCard.from_dict(char_data)
    except Exception:
        pass

if not card:
    card_path = project_root / "characters" / "Кай и Эйла.json"
    if card_path.exists():
        card = TavernParser.parse_json_file(card_path)

persona_id = sess_data.get("user_persona_id")
active_p = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=persona_id)
user_name = active_p.get("name") or "Дэниэл"

world_facts = ""
try:
    from services.rp_cast_manager import RPCastManager
    world_facts = RPCastManager.get_world_state(sess_id)
except Exception:
    pass

# Собираем боевой пакет через RoleplayPromptBuilder
compiled_sys_prompt = ""
if custom_sys:
    compiled_sys_prompt = custom_sys
elif hasattr(RoleplayPromptBuilder, "get_default_system_prompt"):
    compiled_sys_prompt = RoleplayPromptBuilder.get_default_system_prompt(card, user_name=user_name, world_facts=world_facts)
else:
    # Фолбэк на метод build
    history = sess_data.get("messages", [])[:5]
    res = RoleplayPromptBuilder.build(
        character_card=card,
        history=history,
        user_name=user_name,
        world_facts=world_facts
    )
    if isinstance(res, list) and res and res[0].get("role") == "system":
        compiled_sys_prompt = res[0].get("content", "")

print("\n" + "=" * 80)
print(f"БОЕВОЙ СИСТЕМНЫЙ ПРОМПТ (Длина: {len(compiled_sys_prompt)} симв.):")
print("=" * 80)
print(compiled_sys_prompt)
print("=" * 80)
