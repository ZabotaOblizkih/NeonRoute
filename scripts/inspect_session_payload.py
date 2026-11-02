import sys
import json
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

from services.session_manager import SessionManager
from services.tavern_models import CharacterCard
from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

sess_id = "26e7d8f7-76db-429e-9f4e-58f3bfae3260"
sess_dir = project_root / "outputs" / "sessions" / sess_id
sess_file = sess_dir / "session.json"

print("=" * 80)
print(f" ПОЛНЫЙ АУДИТ ПАКЕТА КОНТЕКСТА СЕССИИ: {sess_id}")
print("=" * 80)

if not sess_file.exists():
    print(f"❌ Файл сессии {sess_file} не найден.")
    sys.exit(1)

sess_data = json.loads(sess_file.read_text(encoding="utf-8"))

# 1. Метаданные сессии и оверрайды
print("\n[1. МЕТАДАННЫЕ И ПАРАМЕТРЫ СЕССИИ]:")
print(f"• Персонаж:            {sess_data.get('character_name', 'Не указан')}")
print(f"• user_persona_id:     {sess_data.get('user_persona_id')}")
print(f"• custom_system_prompt: {'Присутствует' if 'custom_system_prompt' in sess_data else 'Отсутствует'}")
print(f"• custom_depth_anchor:  {'Присутствует' if 'custom_depth_anchor' in sess_data else 'Отсутствует'}")
print(f"• Температура (temp):   {sess_data.get('temperature', 'дефолт')}")

# 2. Факты о мире (World State) и активные события
world_facts = ""
try:
    from services.rp_cast_manager import RPCastManager
    world_facts = RPCastManager.get_world_state(sess_id)
    print("\n[2. ДИНАМИЧЕСКИЙ СРЕЗ МИРА (World State / Cast)]:")
    if world_facts:
        print(world_facts)
    else:
        print("• Активных записей состояния мира нет (пусто).")
except Exception as e:
    print(f"• Не удалось получить world_state: {e}")

# 3. Воспоминания из векторной базы LanceDB
try:
    from services.memory_db import MemoryDB
    print("\n[3. ИЗВЛЕЧЕННЫЕ ИЗВЕСТИЯ И ВЕХИ ИЗ LANCEDB]:")
    # Проверяем наличие воспоминаний по ID сессии
    if hasattr(MemoryDB, "get_session_memories"):
        mems = MemoryDB.get_session_memories(sess_id)
        if mems:
            for idx, m in enumerate(mems, 1):
                print(f"  {idx}. {m}")
        else:
            print("• Сохраненных вех в LanceDB для сессии нет.")
    else:
        print("• Метод прямого чтения get_session_memories не объявлен.")
except Exception as e:
    print(f"• Запрос к памяти: {e}")

# 4. Полная хронология сообщений сессии
messages = sess_data.get("messages", [])
print("\n" + "=" * 80)
print(f"[4. ХРОНОЛОГИЯ СООБЩЕНИЙ В session.json (Всего: {len(messages)})]")
print("=" * 80)

for i, msg in enumerate(messages, 1):
    role = msg.get("role", "unknown").upper()
    sender = msg.get("sender", role)
    content = msg.get("content", "")
    print(f"\n--- [#{i}] {role} ({sender}) | Длина: {len(content)} симв. ---")
    print(content)

# 5. Боевая реконструкция последнего вызова LM Studio
print("\n" + "=" * 80)
print("[5. ЧТО БЫЛО ОТПРАВЛЕНО В LM STUDIO НА ПОСЛЕДНЕМ ШАГЕ (messages)]: ")
print("=" * 80)

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
custom_anchor = sess_data.get("custom_depth_anchor")

# Воспроизводим вызов PromptBuilder
last_payload = RoleplayPromptBuilder.build(
    character_card=card,
    history=messages,
    user_name=user_name,
    world_facts=world_facts,
    custom_system_prompt=sess_data.get("custom_system_prompt")
)

# Проверяем пристыковку якоря к последнему сообщению
if last_payload and last_payload[-1]["role"] == "user":
    last_user_query = messages[-1].get("content", "") if messages else ""
    depth_anchor = RoleplayPromptBuilder.build_depth_anchor(
        card,
        user_name=user_name,
        user_query=last_user_query,
        custom_anchor=custom_anchor
    )
    if depth_anchor and not last_payload[-1]["content"].endswith(depth_anchor):
        last_payload[-1]["content"] += depth_anchor

for idx, p_msg in enumerate(last_payload):
    print(f"\n>> PACKET[{idx}] role='{p_msg['role']}' (символов: {len(p_msg['content'])}):")
    # Если это длинный системный промпт, покажем шапку и хвост, сообщения истории покажем целиком
    if p_msg['role'] == 'system' and len(p_msg['content']) > 500:
        print(p_msg['content'][:250] + "\n... [системный промпт 5147 симв.] ...\n" + p_msg['content'][-250:])
    else:
        print(p_msg['content'])

print("=" * 80)
