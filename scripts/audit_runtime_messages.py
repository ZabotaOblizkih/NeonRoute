import sys
import json
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.session_manager import SessionManager
from services.tavern_parser import TavernParser

print("=" * 80)
print("1. АУДИТ РЕАЛЬНЫХ ФАЙЛОВ СЕССИЙ (chat_*.json)")
print("=" * 80)

history_dir = ROOT / "history"
if not history_dir.exists():
    history_dir = ROOT / "data" / "history"

session_files = list(ROOT.rglob("chat_*.json"))
print(f"Найдено файлов сессий: {len(session_files)}")

kai_sessions = []
for sf in session_files:
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        char_name = str(data.get("character_data", {}).get("name") or data.get("title") or "")
        if "кай" in char_name.lower() or "эила" in char_name.lower() or "eyla" in char_name.lower():
            kai_sessions.append((sf, data))
    except Exception:
        pass

if kai_sessions:
    print(f"Найдена активная сессия: {kai_sessions[0][0].name}")
    s_data = kai_sessions[0][1]
    print(f"• custom_system_prompt: {repr(s_data.get('custom_system_prompt'))}")
    print(f"• custom_depth_anchor:  {repr(s_data.get('custom_depth_anchor'))}")
    print(f"• sampling_params:      {s_data.get('sampling_params')}")
    print(f"• is_dm_mode:           {s_data.get('is_dm_mode')}")
    print(f"• Сообщений в истории:  {len(s_data.get('messages', []))}")
else:
    print("Сессии по имени Кай и Эйла не найдено в явном виде. Проверим последнюю измененную сессию:")
    if session_files:
        latest = max(session_files, key=lambda f: f.stat().st_mtime)
        try:
            s_data = json.loads(latest.read_text(encoding="utf-8"))
            print(f"Файл: {latest.name} (Заголовок: {s_data.get('title')})")
            print(f"• custom_system_prompt: {repr(s_data.get('custom_system_prompt'))}")
            print(f"• sampling_params:      {s_data.get('sampling_params')}")
        except Exception as e:
            print(f"Ошибка чтения: {e}")

print("\n" + "=" * 80)
print("2. ПОЛНАЯ ЭМУЛЯЦИЯ SessionManager.get_lm_studio_messages")
print("=" * 80)

card_path = ROOT / "characters" / "Кай и Эйла.json"
card = TavernParser.parse_file(str(card_path))

test_history = [
    {"role": "assistant", "content": "*Рынок рабов гудит...*"},
    {"role": "user", "content": "Я бросаю торговцу деньги"}
]

# Вызываем ровно так, как вызывает RoleplayFlowController на L191
messages = SessionManager.get_lm_studio_messages(
    history=test_history,
    is_roleplay=True,
    character_card=card,
    user_name="Дэниэл",
    history_limit=65
)

print(f"Всего сообщений сформировано: {len(messages)}")
for idx, m in enumerate(messages):
    role = m.get("role", "").upper()
    content = m.get("content", "")
    print(f"\n--- [СООБЩЕНИЕ {idx}] ROLE: {role} (Длина: {len(content)} симв.) ---")
    if role == "USER":
        print("СОДЕРЖИМОЕ USER-СООБЩЕНИЯ:")
        print(content)
    elif role == "SYSTEM" and idx > 0:
        print("ВНУТРЕННИЙ СИСТЕМНЫЙ БЛОК:")
        print(content)
    else:
        # Первые 150 символов
        print(content[:150] + "...")

print("\n" + "=" * 80)
