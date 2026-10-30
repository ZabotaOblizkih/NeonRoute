import sys
import json
import re
import time
import urllib.request
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ТЕСТ: WORLD STATE PROTOCOL & SCENE SNAPSHOT (АРХИТЕКТУРНЫЙ КОНТРАКТ)")
print("=" * 80)

user_name = "Дэниэл"
user_raw_query = "Привет"

# 1. Читаем сырые поля карточки
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

# 2. Программный протокол NeonRoute (Runtime Contract)
runtime_protocol = f"""[NeonRoute Engine Protocol // Roleplay State Machine]
Режим работы: Кинематографичное ранобэ (Third-Person Omniscient Perspective).

[1. СУЩНОСТИ И ГРАНИЦЫ УПРАВЛЕНИЯ]:
• Протагонист: {user_name} (управляется ИСКЛЮЧИТЕЛЬНО пользователем). Модели запрещено генерировать прямую речь, мысли и физические действия за {user_name}.
• Окружение: Одноклассники класса 2-A, салон автобуса, внешний мир. Модель полностью управляет их поведением, эмоциями, паникой и диалогами.

[2. СЛЕПОК ТЕКУЩЕГО КАДРА (SCENE SNAPSHOT)]:
• Локация: Салон экскурсионного автобуса, застывшего посреди бескрайней незнакомой равнины.
• Временная шкала: Пролог полностью завершён. Сион только что исчезла. В салоне повисла звенящая тишина и шок.
• Событие триггера: {user_name} нарушает общую тишину негромким приветствием со своего места.

[3. ПАЙПЛАЙН ГЕНЕРАЦИИ ОТВЕТА (EXECUTION PIPELINE)]:
Шаг 1. Физика кадра: Опиши акустику салона, эхо голоса в тишине, солнечный свет через пыльные стекла и оцепенение подростков.
Шаг 2. Агентность окружения: Покажи реакцию конкретных одноклассников рядом с {user_name} (вздрагивание, шепот, растерянность, мимика).
Шаг 3. Передача хода: Заверши сцену прямым обращением или вопросом конкретного одноклассника к {user_name}. Не используй мета-комментариев ведущего.
Шаг 4. Системный оверлей: Заверши ход выводом актуального окна статуса персонажа внутри тегов <stats>```...```</stats>."""

final_system_prompt = f"{raw_card_prompt}\n\n{runtime_protocol}"

# 3. Лаконичный Depth-0 триггер без провоцирующих отрицаний
depth_anchor = (
    f"[Engine Step: Реплика «{user_raw_query}» произнесена {user_name} в салон автобуса сразу после исчезновения Сион. "
    f"Выполни Pipeline: реакция класса в 3-м лице -> вопрос/действие одноклассника к {user_name} -> блок <stats>.]"
)

effective_user_query = f"{user_raw_query}\n\n{depth_anchor}"

messages = [
    {"role": "system", "content": final_system_prompt},
    {"role": "assistant", "content": first_mes_clean},
    {"role": "user", "content": effective_user_query}
]

payload = {
    "model": "Qwen2.5-VL-7B-Instruct-GGUF",
    "messages": messages,
    "temperature": 0.7,
    "top_p": 0.90,
    "min_p": 0.08,
    "repetition_penalty": 1.05,
    "repeat_penalty": 1.05,
    "dry_multiplier": 0.5,
    "dry_base": 1.75,
    "dry_allowed_length": 2,
    "dry_penalty_last_n": 0,
    "xtc_probability": 0.0,
    "cache_prompt": True,
    "max_tokens": 1024,
    "stream": True
}

print(f"• Исходный ввод:    «{user_raw_query}»")
print(f"• Протагонист:      {user_name}")
print(f"• System Prompt:    {len(final_system_prompt)} симв.")
print(f"• first_mes:        {len(first_mes_clean)} симв.")
print(f"• Sampler:          Temp {payload['temperature']} | Min-P {payload['min_p']} | Top-P {payload['top_p']} | XTC Off")
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
