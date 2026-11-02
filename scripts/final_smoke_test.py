import sys
import json
import requests
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser
from services.session_manager import SessionManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder

LM_STUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"

# 1. Загрузка карточки
card_path = ROOT / "characters" / "Кай и Эйла.json"
card = TavernParser.parse_file(str(card_path))
user_name = "Дэниэл"
user_text = "Я бросаю торговцу деньги"

history = [
    {"role": "assistant", "content": "*Рынок рабов гудит грубыми шутками, звоном цепей и азартным торгом. В одном из рядов стоит железная клетка...*"},
    {"role": "user", "content": user_text}
]

print("=" * 80)
print("ФИНАЛЬНЫЙ РАНТАЙМ-ТЕСТ: ПРОВЕРКА ОБНОВЛЕННОГО ПАЙПЛАЙНА")
print("=" * 80)

# Эмуляция SessionManager.get_lm_studio_messages (после очистки ContextWindowBuilder)
messages = SessionManager.get_lm_studio_messages(
    history=history,
    is_roleplay=True,
    character_card=card,
    user_name=user_name,
    history_limit=65
)

# Эмуляция Depth-1 инъекции из RoleplayFlowController
depth_anchor = RoleplayPromptBuilder.build_depth_anchor(
    card,
    user_name=user_name,
    user_query=user_text,
    custom_anchor=None
)

if depth_anchor and depth_anchor.strip():
    for idx in reversed(range(len(messages))):
        if messages[idx].get("role") == "user":
            anchor_msg = {"role": "system", "content": depth_anchor.strip()}
            messages.insert(idx, anchor_msg)
            break

print("ИТОГОВАЯ СТРУКТУРА ПАКЕТА ДЛЯ LM STUDIO:")
for i, m in enumerate(messages):
    role = m['role'].upper()
    c = m['content'].strip().replace('\n', ' ')
    preview = (c[:95] + "...") if len(c) > 95 else c
    print(f"  [{i}] ROLE: {role:<9} | {preview}")

print("-" * 80)
last_user = [m for m in messages if m["role"] == "user"][-1]
print(f"Чистота ввода игрока: {repr(last_user['content'])}")
assert last_user["content"] == user_text, "ВНИМАНИЕ: реплика игрока была модифицирована!"
print(" Проверка чистоты реплики пройдена успешно.")
print("-" * 80)

# 2. Живой инференс Meissa
payload = {
    "model": "meissa-qwen2.5-7b-instruct",
    "messages": messages,
    "temperature": 0.65,
    "top_p": 0.95,
    "min_p": 0.05,
    "repeat_penalty": 1.02,
    "stream": True,
    "max_tokens": 800
}

print("ГЕНЕРАЦИЯ ОТВЕТА МОДЕЛИ:\n")
try:
    resp = requests.post(LM_STUDIO_URL, json=payload, stream=True, timeout=60)
    for line in resp.iter_lines():
        if line:
            decoded = line.decode("utf-8")
            if decoded.startswith("data: "):
                data_str = decoded[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    delta = json.loads(data_str)["choices"][0]["delta"]
                    print(delta.get("content") or "", end="", flush=True)
                except Exception:
                    pass
    print("\n" + "=" * 80)
except Exception as e:
    print(f"\n❌ Ошибка обращения к LM Studio: {e}\n(Если сервер не запущен, проверь только структуру массива выше)")
