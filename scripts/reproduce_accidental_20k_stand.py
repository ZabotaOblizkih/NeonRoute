import sys
import json
import re
import time
import urllib.request
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" РЕПРОДУКЦИЯ: ВОССОЗДАНИЕ СТЕНДА С УТЕЧКОЙ 20 КБ КОДА (STAND_TEST_ISEKAI_ONLY)")
print("=" * 80)

user_name = "Дэниэл"
user_raw_query = "Привет"

# 1. Загрузка карточки Isekai RPG
card_path = project_root / "characters" / "Isekai RPG.json"
with open(card_path, "r", encoding="utf-8") as f:
    raw_json = json.load(f)

card_data = raw_json.get("data", raw_json)
card_name = card_data.get("name", "Isekai RPG")
card_sys = card_data.get("system_prompt", "").strip()
desc = card_data.get("description", "").strip()
personality = card_data.get("personality", "").strip()
scenario = card_data.get("scenario", "").strip()

first_mes = (card_data.get("first_mes_ru") or card_data.get("first_mes", "")).strip()
first_mes_clean = re.sub(r'[\u4e00-\u9fff]+', '', first_mes)
first_mes_clean = re.sub(r' +', ' ', first_mes_clean).strip()

card_blocks = []
if card_sys:
    card_blocks.append(card_sys)
if desc:
    card_blocks.append(f"[Description of {card_name}]:\n{desc}")
if personality:
    card_blocks.append(f"[Personality / Mechanics of {card_name}]:\n{personality}")
if scenario:
    card_blocks.append(f"[Scenario]:\n{scenario}")

raw_card_prompt = "\n\n".join(card_blocks)

# 2. Воспроизведение захвата кода из roleplay_prompt_builder.py
builder_file = project_root / "services" / "roleplay_prompt_builder.py"
builder_content = builder_file.read_text(encoding="utf-8")

# Та самая регулярка, зацепившая 20 КБ структуры движка:
leak_match = re.search(r'"""[\s\S]*?(?:stats|кубик|dnd|литрпг|isekai)[\s\S]*?"""', builder_content, re.IGNORECASE)

if leak_match:
    leaked_payload = leak_match.group(0)
    print(f"• Захвачен блок кода из builder: {len(leaked_payload)} симв. (ожидалось ~20 473)")
else:
    # Запасной срез первых 20 КБ файла, если регулярка не совпала
    leaked_payload = builder_content[:20473]
    print(f"• Срез первых 20 КБ builder:     {len(leaked_payload)} симв.")

final_system_prompt = f"{raw_card_prompt}\n\n{leaked_payload}"

# 3. Исходный Depth-0 якорь из того запуска
depth_anchor_original = (
    f"[Фокус Рассказчика: {user_name} произносит реплику в пространство. "
    f"Опиши отклик окружения и реакцию присутствующих NPC в третьем лице. "
    f"Заверши ответ обращением или вопросом NPC к {user_name}, передав ему инициативу. "
    f"Категорически запрещено отвечать от лица {user_name} и задавать мета-вопросы от себя («Что будешь делать? / Давай обсудим»). "
    f"Обязательно заверши сообщение актуальным блоком характеристик <stats>...</stats>.]"
)

effective_user_query = f"{user_raw_query}\n\n{depth_anchor_original}"

messages = [
    {"role": "system", "content": final_system_prompt},
    {"role": "assistant", "content": first_mes_clean},
    {"role": "user", "content": effective_user_query}
]

# Исходный профиль сэмплирования
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

print(f"• Исходный ввод:    «{user_raw_query}»")
print(f"• System Prompt:    {len(final_system_prompt)} симв.")
print(f"• first_mes:        {len(first_mes_clean)} симв.")
print(f"• Sampler:          Temp 1.0 | XTC 0.3 | Min-P 0.05 | DRY 0.8")
print("-" * 80)

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
