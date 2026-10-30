import sys
import json
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print("1. РАЗБОР ФАЙЛА СЕССИИ abf6f6fb...")
print("=" * 80)
sess_path = project_root / "outputs" / "sessions" / "abf6f6fb-f96e-4e5b-8d93-c32221ec238d" / "session.json"
if not sess_path.exists():
    print(f"❌ Файл {sess_path} не найден! Ищем папку в outputs/sessions:")
    candidates = list((project_root / "outputs" / "sessions").glob("*/session.json"))
    sess_path = candidates[-1] if candidates else None

if sess_path and sess_path.exists():
    print(f"✅ Открыт файл: {sess_path}")
    data = json.loads(sess_path.read_text(encoding="utf-8"))
    print(f"Имя карточки в сессии: {data.get('character_name')}")
    print(f"Режим: {data.get('mode')}")
    history = data.get("history", [])
    print(f"Всего сообщений в истории: {len(history)}")
    for i, m in enumerate(history):
        role = m.get("role")
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        print(f"\n[{i}] ROLE: {role} (длина: {len(c)} симв.)")
        print(f"    {c[:350]}...")
else:
    print("❌ Не найдено ни одного session.json")

print("\n" + "=" * 80)
print("2. ОКОНЧАНИЕ СБОРКИ В ContextWindowBuilder.get_lm_studio_messages (строки 420-510)")
print("=" * 80)
p_cwb = Path("services/context_window_builder.py")
lines = p_cwb.read_text(encoding="utf-8").splitlines()
for i in range(419, min(len(lines), 510)):
    print(f"{i+1:4d}: {lines[i]}")

print("\n" + "=" * 80)
print("3. РЕКОНСТРУКЦИЯ ТОЧНОГО МАССИВА MESSAGES ДЛЯ СЕССИИ")
print("=" * 80)
if sess_path and sess_path.exists():
    from services.tavern_parser import TavernParser
    from services.context_window_builder import ContextWindowBuilder

    card = TavernParser.parse_file("characters/Isekai RPG.json")
    built_msgs = ContextWindowBuilder.get_lm_studio_messages(
        history=history,
        is_roleplay=True,
        character_card=card,
        user_name=data.get("user_name", "Игрок"),
        session_id=data.get("id")
    )
    print(f"Сформировано сообщений для отправки в llama-server: {len(built_msgs)}")
    for idx, bm in enumerate(built_msgs):
        r = bm.get("role")
        cnt = bm.get("content", "")
        print(f"\n--- [LLM Input Message {idx}] ROLE: '{r}' | ДЛИНА: {len(cnt)} симв. ---")
        print(f"Начало:\n{cnt[:250]}")
        print(f"...\nКонец:\n{cnt[-250:]}")
