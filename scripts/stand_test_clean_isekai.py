import sys
import json
import time
import urllib.request
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ЧИСТОВОЙ ТЕСТ: СЫРАЯ КАРТОЧКА + ЧИСТЫЙ СИСТЕМНЫЙ БЛОК + DEPTH-ЯКОРЬ")
print("=" * 80)

user_name = "Дэниэл"
user_raw_query = "Привет"

# 1. Читаем сырые поля карточки напрямую
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

# 2. Находим ЧИСТЫЙ системный блок правил ЛитРПГ в roleplay_prompt_builder
import services.roleplay_prompt_builder as rpb

clean_rule = ""
source_name = ""

# Ищем в словарях и атрибутах модуля/класса
builder_cls = getattr(rpb, "RoleplayPromptBuilder", None)
search_scopes = [vars(rpb)]
if builder_cls:
    search_scopes.append(vars(builder_cls))

for scope in search_scopes:
    for k, v in scope.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, str) and any(w in str(sub_k).lower() for w in ("isekai", "litrpg", "system")):
                    clean_rule = sub_v.strip()
                    source_name = f"{k}['{sub_k}']"
                    break
        elif isinstance(v, str) and any(w in k.lower() for w in ("isekai", "litrpg", "system_prompt")):
            if len(v.strip()) > 100 and not v.strip().startswith("import "):
                clean_rule = v.strip()
                source_name = k
                break
    if clean_rule:
        break

# Если правила собираются методами динамически — используем канонический чистый блок ЛитРПГ
if not clean_rule:
    source_name = "Канонический блок ЛитРПГ NeonRoute (чистый)"
    clean_rule = (
        "[Системные правила ЛитРПГ / Исекай]:\n"
        "1. Ты выступаешь в роли бесплотной игровой Системы и Рассказчика мира. Веди повествование строго от третьего лица.\n"
        f"2. Категорически запрещено отыгрывать действия, прямую речь или мысли за персонажа игрока ({user_name}).\n"
        "3. Запрещены упоминания бросков кубиков, механик D&D (d20) и оффтоп-комментарии ведущего.\n"
        "4. В конце каждого сюжетно значимого ответа обязательно выводи актуальное окно характеристик строго внутри тегов:\n"
        "<stats>\n"
        "```\n"
        "Физическое состояние: ...\n"
        "Инвентарь: [...]\n"
        "Оборудование: [...]\n"
        "Класс: ...\n"
        "Золото: ...\n"
        "Уровень: ...\n"
        "BP: ...\n"
        "Опыт: ...\n"
        "Репутация: ...\n"
        "Навыки: ...\n"
        "Локация: ...\n"
        "Уровень угрозы: ...\n"
        "```\n"
        "</stats>\n"
        f"5. Обращайся к игроку ({user_name}) в мужском роде."
    )

print(f"[Источник системного правила]: {source_name}")
print(f"[Длина правила]: {len(clean_rule)} символов (вместо прошлых 20 473 байт кода!)")
print("-" * 80)
print("ТЕКСТ СИСТЕМНОГО ПРАВИЛА:")
print(clean_rule)
print("-" * 80)

# 3. Собираем чистый системный промпт без единой строчки Python
final_system_prompt = f"{raw_card_prompt}\n\n{clean_rule}"

# 4. Проверенный Depth-0 якорь
depth_anchor = (
    f"[Фокус Рассказчика: {user_name} произносит реплику в пространство. "
    f"Опиши отклик окружения и реакцию присутствующих NPC в третьем лице. "
    f"Заверши ответ обращением или вопросом NPC к {user_name}, передав ему инициативу. "
    f"Категорически запрещено отвечать от лица {user_name} и задавать мета-вопросы от себя («Что будешь делать? / Давай обсудим»). "
    f"Обязательно заверши сообщение актуальным блоком характеристик <stats>...</stats>.]"
)

effective_user_query = f"{user_raw_query}\n\n{depth_anchor}"

messages = [
    {"role": "system", "content": final_system_prompt},
    {"role": "assistant", "content": first_mes},
    {"role": "user", "content": effective_user_query}
]

print(f"• Исходный ввод в UI:      «{user_raw_query}»")
print(f"• Имя протагониста:        {user_name}")
print(f"• Длина System Prompt:     {len(final_system_prompt)} симв. (Карточка ~8K + Правило ~1K)")
print(f"• first_mes (автобус):     {len(first_mes)} симв.")
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
        print("\n" + "=" * 80)
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
