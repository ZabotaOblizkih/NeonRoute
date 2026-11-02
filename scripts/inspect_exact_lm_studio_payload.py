import sys
import json
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.session_manager import SessionManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.tavern_parser import TavernParser

SESS_ID = "6327f53c-db38-40a3-8dbf-c6e7076c164e"
sess_file = ROOT / "history" / f"chat_{SESS_ID}.json"

if not sess_file.exists():
    matches = list((ROOT / "history").glob(f"*{SESS_ID}*.json"))
    if matches:
        sess_file = matches[0]
    else:
        print(f"❌ Файл сессии {SESS_ID} не найден!")
        sys.exit(1)

sess_data = json.loads(sess_file.read_text(encoding="utf-8"))

print("=" * 80)
print(f"РАСПАКОВКА СЕССИИ: {sess_file.name}")
print("=" * 80)

raw_msgs = sess_data.get("messages", [])
history_before_reply = []
for m in raw_msgs:
    txt = str(m.get("text") or m.get("content") or "")
    if ("эмансипация" in txt.lower() or "огры" in txt.lower()) and (m.get("role") == "assistant" or not m.get("is_user")):
        break
    
    role = m.get("role")
    if not role:
        role = "user" if m.get("is_user") else "assistant"
        
    history_before_reply.append({
        "role": str(role),
        "content": txt
    })

print(f"Ходов в истории перед генерацией: {len(history_before_reply)}")
for idx, h in enumerate(history_before_reply):
    r_str = str(h.get('role') or 'unknown').upper()
    c_preview = h['content'][:70].replace('\n', ' ')
    print(f"  [{idx}] {r_str}: {c_preview}...")

card_path = sess_data.get("character_file") or (ROOT / "characters" / "Кай и Эйла.json")
card = TavernParser.parse_file(str(card_path)) if Path(card_path).exists() else None

user_name = sess_data.get("user_persona_name") or "Дэниэл"
custom_sys = sess_data.get("custom_system_prompt")
custom_anchor = sess_data.get("custom_depth_anchor")

messages = SessionManager.get_lm_studio_messages(
    history=history_before_reply,
    is_roleplay=True,
    character_card=card,
    user_name=user_name,
    history_limit=65,
    session_id=SESS_ID,
    custom_system_prompt=custom_sys
)

user_query = history_before_reply[-1]["content"] if history_before_reply else ""
depth_anchor = RoleplayPromptBuilder.build_depth_anchor(
    card,
    user_name=user_name,
    user_query=user_query,
    custom_anchor=custom_anchor
)

if depth_anchor and depth_anchor.strip():
    for idx in reversed(range(len(messages))):
        if messages[idx].get("role") == "user":
            anchor_msg = {"role": "system", "content": depth_anchor.strip()}
            messages.insert(idx, anchor_msg)
            break

dump_path = ROOT / "last_payload_dump.txt"
with open(dump_path, "w", encoding="utf-8") as f:
    for idx, m in enumerate(messages):
        f.write(f"\n{'='*40} [MESSAGE {idx} | ROLE: {str(m.get('role')).upper()}] {'='*40}\n")
        f.write(str(m.get("content", "")))
        f.write("\n")

print("\n" + "=" * 80)
print(f"СТРУКТУРА ПАКЕТА ДЛЯ LM STUDIO (Всего блоков: {len(messages)})")
print("=" * 80)

for idx, m in enumerate(messages):
    role = str(m.get('role', 'unknown')).upper()
    content = str(m.get('content', ''))
    length = len(content)
    print(f"\n--- [БЛОК {idx}] ROLE: {role} (Длина: {length} симв.) ---")
    if role == "SYSTEM" and idx == 0:
        lines = content.splitlines()
        headers = [l for l in lines if l.startswith("#") or l.startswith("###")]
        print("Разделы системного промпта:")
        for h in headers:
            print(f"  • {h}")
        print("\nПервые 300 символов:")
        print(content[:300] + "...")
    else:
        print(content)

print("\n" + "=" * 80)
print(f"Полный дамп сохранён: {dump_path}")
print("=" * 80)
