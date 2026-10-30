import sys
import json
import re
import time
import urllib.request
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ТЕСТ: РУССКАЯ КАРТОЧКА + КИНЕМАТОГРАФИЧЕСКОЕ РАНОБЭ + СЕНСОРНЫЙ ЯКОРЬ")
print("=" * 80)

user_name = "Дэниэл"
user_raw_query = "Привет"

card_path = project_root / "characters" / "Isekai RPG.json"
with open(card_path, "r", encoding="utf-8") as f:
    raw_json = json.load(f)

card_data = raw_json.get("data", raw_json)
card_name = card_data.get("name", "Isekai RPG")
card_sys = card_data.get("system_prompt", "").strip()
desc = card_data.get("description", "").strip()
personality = card_data.get("personality", "").strip()
scenario = card_data.get("scenario", "").strip()

# Берем русскую версию first_mes
first_mes = (card_data.get("first_mes_ru") or card_data.get("first_mes", "")).strip()

# Санитизация: удаляем случайные китайские иероглифы автоперевода, сбивающие токенизатор
first_mes_clean = re.sub(r'[\u4e00-\u9fff]+', '', first_mes)
# Убираем двойные пробелы после вычистки
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

# Системный блок NeonRoute: КИНЕМАТОГРАФИЧНОЕ РАНОБЭ
cinematic_litrpg_rule_ru = (
    "[Системный манифест ЛитРПГ / Кинематографичное ранобэ]:\n"
    "1. Ты — бесплотная игровая Система и всевидящий Рассказчик. Веди повествование строго от третьего лица в прошедшем или настоящем времени.\n"
    "2. Стиль: высокодетализированная художественная проза в жанре японского исекай-ранобэ. Насыщай сцену сенсорными деталями: звуки (скрип металла, прерывистое дыхание), свет и тени, запахи пыли или озона, тактильные ощущения.\n"
    "3. Окружение и NPC: одноклассники, водитель Танака и спутники — это не безликая масса, а живые подростки со своими страхами, именами, паникой и недоверием. Показывай их мимику, жесты и спонтанные реплики.\n"
    f"4. Категорически запрещено совершать действия, произносить реплики или описывать ход мыслей за персонажа игрока ({user_name}). Только реакция мира на него.\n"
    "5. Запрещены мета-комментарии от лица ведущего («Что будешь делать? / Давай начнем»). Передавай инициативу через прямую речь NPC или изменение обстановки.\n"
    "6. В конце сюжетного ответа обязательно выводи актуальное окно характеристик строго внутри тегов:\n"
    "<stats>\n"
    "```\n"
    "Физическое состояние: ...\n"
    "Инвентарь: [...]\n"
    "Оборудование: [...]\n"
    "Класс: ...\n"
    "Золото: 0G\n"
    "Уровень: 1\n"
    "BP: ...\n"
    "Опыт: 0/100\n"
    "Репутация: ...\n"
    "Навыки: ...\n"
    "Локация: ...\n"
    "Уровень угрозы: ...\n"
    "```\n"
    "</stats>"
)

final_system_prompt = f"{raw_card_prompt}\n\n{cinematic_litrpg_rule_ru}"

# Локальный Depth-0 якорь с сенсорным вектором
depth_anchor_cinematic = (
    f"[Директива Рассказчика: {user_name} нарушает оцепенение и негромко произносит реплику в салон автобуса. "
    f"Опиши физический отклик пространства и нарастающую тревогу в классе через сенсорные детали (звуки, свет, взгляды). "
    f"Покажи реакцию конкретных одноклассников (паника, шепот, растерянность). "
    f"Заверши сцену прямым вопросом или действием конкретного NPC к {user_name}, передав ему ход. "
    f"Ни единого слова или действия от лица {user_name}. "
    f"В самом конце обязательно выведи блок <stats>...</stats> на русском языке.]"
)

effective_user_query = f"{user_raw_query}\n\n{depth_anchor_cinematic}"

messages = [
    {"role": "system", "content": final_system_prompt},
    {"role": "assistant", "content": first_mes_clean},
    {"role": "user", "content": effective_user_query}
]

print(f"• Исходный ввод:          «{user_raw_query}»")
print(f"• Протагонист:            {user_name}")
print(f"• first_mes (очищен):     {len(first_mes_clean)} симв.")
print(f"• System Prompt:          {len(final_system_prompt)} симв.")
print("-" * 80)

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
