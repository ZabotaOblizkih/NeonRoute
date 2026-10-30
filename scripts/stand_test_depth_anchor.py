import sys
import json
import time
import urllib.request
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ТЕСТ: РЕПЛИКА «Привет» С ИМЕНЕМ «Дэниэл» И УТОЧНЁННЫМ ЯКОРЕМ")
print("=" * 80)

from services.tavern_parser import TavernParser
from services.context_window_builder import ContextWindowBuilder

card_path = project_root / "characters" / "Isekai RPG.json"
card = TavernParser.parse_file(str(card_path))
user_name = "Дэниэл"

user_raw_query = "Привет"

# Уточнённый якорь: передача инициативы игроку и строгая изоляция действий
depth_anchor = (
    f"[Фокус Рассказчика: {user_name} произносит реплику в пространство. "
    f"Опиши отклик окружения и реакцию присутствующих NPC в третьем лице. "
    f"Заверши ответ обращением или вопросом NPC к {user_name}, передав ему ход. "
    f"Категорически запрещено описывать мысли, речь или телодвижения за {user_name}.]"
)

effective_user_content = f"{user_raw_query}\n\n{depth_anchor}"

sys_prompt = ContextWindowBuilder.build_system_prompt(
    character_card=card,
    user_name=user_name,
    is_roleplay=True,
    is_engineer=False
)
first_mes = getattr(card, "first_mes_ru", None) or getattr(card, "first_mes", "") or ""

messages = [
    {"role": "system", "content": sys_prompt},
    {"role": "assistant", "content": first_mes},
    {"role": "user", "content": effective_user_content}
]

print(f"[Пользователь]: {user_name}")
print(f"[Реплика]: «{user_raw_query}»")
print(f"[Runtime-якорь]:\n{depth_anchor}\n")

payload = {
    "model": "Qwen2.5-VL-7B-Instruct-GGUF",
    "messages": messages,
    "temperature": 1.0,
    "top_p": 0.95,
    "min_p": 0.05,
    "repetition_penalty": 1.02,
    "repeat_penalty": 1.02,
    "dry_multiplier": 0.8,
    "dry_base": 1.75,
    "dry_allowed_length": 2,
    "dry_penalty_last_n": 0,
    "xtc_probability": 0.3,
    "xtc_threshold": 0.1,
    "cache_prompt": True,
    "max_tokens": 1024,
    "stream": True
}

req = urllib.request.Request(
    "http://127.0.0.1:1234/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

start_time = time.time()
first_token_time = None
full_reply = []

try:
    with urllib.request.urlopen(req, timeout=120) as response:
        print("=" * 80)
        print(" СЫРОЙ ВЫВОД МОДЕЛИ (STREAM):")
        print("=" * 80)
        for line in response:
            line_str = line.decode("utf-8").strip()
            if not line_str or line_str == "data: [DONE]":
                continue
            if line_str.startswith("data: "):
                try:
                    chunk = json.loads(line_str[6:])
                    content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if content:
                        if first_token_time is None:
                            first_token_time = time.time() - start_time
                        sys.stdout.write(content)
                        sys.stdout.flush()
                        full_reply.append(content)
                except Exception:
                    pass

        total_time = time.time() - start_time
        reply_text = "".join(full_reply)
        print("\n" + "=" * 80)
        print(f" МЕТРИКИ: TTFT: {first_token_time:.2f}с | Время: {total_time:.2f}с | Длина: {len(reply_text)} симв.")
        print("=" * 80)
except Exception as e:
    print(f"\n❌ Ошибка вызова: {e}")
