import sys
import json
import inspect
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

from services.session_manager import SessionManager
from services.tavern_models import CharacterCard
from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

sess_id = "26e7d8f7-76db-429e-9f4e-58f3bfae3260"
sess_file = project_root / "outputs" / "sessions" / sess_id / "session.json"

if not sess_file.exists():
    print(f"❌ Файл сессии {sess_file} не найден.")
    sys.exit(1)

sess_data = json.loads(sess_file.read_text(encoding="utf-8"))
raw_messages = sess_data.get("messages", [])

print("=" * 80)
print(f" ХРОНОЛОГИЯ РЕПЛИК СЕССИИ {sess_id} (Всего: {len(raw_messages)})")
print("=" * 80)

for idx, m in enumerate(raw_messages, 1):
    sender = m.get("sender") or m.get("role") or "Unknown"
    # Извлекаем текст реплики из возможных полей
    text = m.get("text") or m.get("content") or ""
    print(f"\n[#{idx}] {sender}:")
    print(text.strip())

print("\n" + "=" * 80)
print(" СИГНАТУРА RoleplayPromptBuilder.build:")
print("=" * 80)
sig = inspect.signature(RoleplayPromptBuilder.build)
print(f"build{sig}")

print("\n" + "=" * 80)
print(" ТОЧНЫЙ ПАКЕТ ДЛЯ LM STUDIO (ПОСЛЕДНИЙ ШАГ СЕССИИ)")
print("=" * 80)

# Подготовка карточки
card_data = sess_data.get("character_data")
card = CharacterCard.from_dict(card_data) if card_data else TavernParser.parse_json_file(project_root / "characters" / "Кай и Эйла.json")

# Имя игрока
active_p = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=sess_data.get("user_persona_id"))
user_name = active_p.get("name") or "Дэниэл"

# Факты о мире
world_facts = ""
try:
    from services.rp_cast_manager import RPCastManager
    world_facts = RPCastManager.get_world_state(sess_id)
except Exception:
    pass

# Вызываем build сопоставляя параметры
build_params = sig.parameters
kwargs = {}
if "messages" in build_params:
    kwargs["messages"] = raw_messages
elif "history" in build_params:
    kwargs["history"] = raw_messages
elif "session_messages" in build_params:
    kwargs["session_messages"] = raw_messages

if "character_card" in build_params:
    kwargs["character_card"] = card
if "user_name" in build_params:
    kwargs["user_name"] = user_name
if "world_facts" in build_params:
    kwargs["world_facts"] = world_facts
if "session_id" in build_params:
    kwargs["session_id"] = sess_id

try:
    if not kwargs:
        payload = RoleplayPromptBuilder.build(card, raw_messages, user_name=user_name, world_facts=world_facts)
    else:
        payload = RoleplayPromptBuilder.build(**kwargs)

    for i, p in enumerate(payload):
        role = p.get("role", "").upper()
        content = p.get("content", "")
        print(f"\n--- [LLM PACKET #{i+1}] ROLE: {role} (Длина: {len(content)} симв.) ---")
        if role == "SYSTEM":
            # Шапка системного промпта
            print(content[:300] + f"\n... [скрыто {len(content) - 600} симв.] ...\n" + content[-300:])
        else:
            print(content)
except Exception as e:
    print(f"Ошибка сборки через build: {e}")
    # Выведем исходный код build, чтобы сразу увидеть логику
    print(inspect.getsource(RoleplayPromptBuilder.build))

print("=" * 80)
