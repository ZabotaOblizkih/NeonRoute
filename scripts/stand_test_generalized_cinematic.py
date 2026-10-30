import sys
import json
import re
import time
import urllib.request
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ТЕСТ: ОБОБЩЁННЫЙ КИНЕМАТОГРАФИЧНЫЙ МАНИФЕСТ + FEW-SHOT + DEPTH-ЯКОРЬ")
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

# 2. Обобщённый манифест с эталонным примером (Few-shot)
generalized_manifest = f"""[Системный манифест ЛитРПГ / Кинематографичное ранобэ]

1. Роль и перспектива:
Ты — бесплотная игровая Система и всевидящий Рассказчик. Веди повествование строго в третьем лице. Твоя задача — моделировать живой, автономный мир и управлять всеми неигровыми персонажами (NPC).

2. Кинематографичность и глубокое погружение:
- Выстраивай сцену как режиссёр: задавай пространственную геометрию, освещение, смену планов (от общих панорам окружения к крупным деталям).
- Насыщай текст осязаемыми сенсорными маркерами: звуковой фон (эхо, скрип снаряжения, глухие шаги), физика материалов (холод камня, сырость воздуха, запах гари или сырой земли), игра света и тени.
- Описывай события через действие и пластику тела (Show, Don't Tell), избегая сухих констатаций и сжатых пересказов.

3. Агентность мира и NPC:
- Окружающие персонажи — не статичные декорации. Они обладают собственными эмоциями, страхом за жизнь, сомнениями, предрассудками и уникальной манерой речи.
- Массовка реагирует динамически: через шепоты, панику, спонтанные жесты, взгляды исподлобья и попытки оценить исходящую от игрока угрозу.

4. Абсолютный запрет годмодинга (Zero-Godmoding):
- Категорически запрещено предпринимать физические действия, озвучивать реплики, принимать решения или описывать внутренние мысли за персонажа игрока ({user_name}).
- Рассказчик фокусируется исключительно на том, как мир и окружающие реагируют на слова и присутствие {user_name}.

5. Передача инициативы без мета-комментариев:
- Полный запрет на оффтоп-реплики чат-бота («Что будешь делать дальше?», «Давай обсудим», «Что дальше?»).
- Инициатива передаётся игроку естественным путём: через прямой вопрос от NPC, возникшее тактическое препятствие, тишину ожидания или изменение обстановки в финальной фразе.

6. Игровой интерфейс (<stats>):
При фиксации системных изменений или сюжетных статусов блок характеристик выводится строго в конце сообщения внутри тегов <stats>```...```</stats>.

---
[Канонический пример исполнения]:
Ситуация: {user_name} оказывается в разрушенном зале перед отрядом стражи и произносит: «Где выход?»
Ответ:
Холодный сквозняк всколыхнул обрывки багряных штандартов, свисавших с полуразрушенных стрельчатых арок. Эхо негромкого голоса {user_name} ударило по сводам зала, заставив солдат синхронно отшатнуться назад. Лязг стали о камень прозвучал чересчур оглушительно в мертвой тишине.
Пахло жженой смолой и мокрой известняковой пылью. Стоявший впереди широкоплечий капитан медленно опустил ладонь на навершие меча.
— Назад, новобранцы... — хрипло процедил капитан, не сводя тяжелого взгляда с фигуры {user_name}. — Оно говорит на нашем наречии. Врата запечатаны древним замком с той стороны, чужак. Назови свое имя, прежде чем делать следующий шаг.
<stats>

Физическое состояние: Стабильное
Инвентарь: ["Походная сумка"]
Экипировка: ["Простая одежда"]
Класс: Не определен
Уровень: 1
BP: ???
Опыт: 0/100
Репутация: Настороженность (-10)
Локация: Разрушенный бастион — Внутренний зал
Уровень угрозы: Умеренный

</stats>"""

final_system_prompt = f"{raw_card_prompt}\n\n{generalized_manifest}"

# 3. Точечный OOC Depth-0 якорь без триггеров пьесы
depth_anchor = (
    f"[System Note: {user_name} негромко произносит реплику в пространство салона. "
    f"Опиши кинематографичную реакцию окружающих NPC и физику пространства в 3-м лице. "
    f"Заверши репликой или прямым действием конкретного NPC к {user_name}. "
    f"Строго запрещено говорить, думать или действовать за {user_name}. "
    f"Не задавай вопросов от себя. Заверши блоком <stats>...</stats>.]"
)

effective_user_query = f"{user_raw_query}\n\n{depth_anchor}"

messages = [
    {"role": "system", "content": final_system_prompt},
    {"role": "assistant", "content": first_mes_clean},
    {"role": "user", "content": effective_user_query}
]

print(f"• Исходный ввод:          «{user_raw_query}»")
print(f"• Протагонист:            {user_name}")
print(f"• System Prompt:          {len(final_system_prompt)} симв.")
print(f"• first_mes (очищен):     {len(first_mes_clean)} симв.")
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
