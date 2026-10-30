import sys
import json
import time
import urllib.request
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

card_path = project_root / "characters" / "Isekai RPG.json"
with open(card_path, "r", encoding="utf-8") as f:
    raw_json = json.load(f)

card_data = raw_json.get("data", raw_json)
card_name = card_data.get("name", "Isekai RPG")
card_sys = card_data.get("system_prompt", "").strip()
desc = card_data.get("description", "").strip()
personality = card_data.get("personality", "").strip()
scenario = card_data.get("scenario", "").strip()

# Берем СТРОГО оригинальный английский first_mes (без китайских иероглифов и кривого перевода)
first_mes_en = card_data.get("first_mes", "").strip()

card_blocks = []
if card_sys:
    card_blocks.append(card_sys)
if desc:
    card_blocks.append(f"[Description of {card_name}]:\n{desc}")
if personality:
    card_blocks.append(f"[Personality / Mechanics of {card_name}]:\n{personality}")
if scenario:
    card_blocks.append(f"[Scenario]:\n{scenario}")

pure_en_card_prompt = "\n\n".join(card_blocks)

# Чистый системный блок ЛитРПГ NeonRoute
clean_rule_ru = (
    "[Системные правила ЛитРПГ / Исекай]:\n"
    "1. Ты выступаешь в роли бесплотной игровой Системы и Рассказчика мира. Веди повествование строго от третьего лица.\n"
    "2. Запрещены упоминания бросков кубиков и механик D&D (d20).\n"
    "3. В конце каждого сюжетно значимого ответа обязательно выводи актуальное окно характеристик строго внутри тегов:\n"
    "<stats>\n```\n...\n```\n</stats>"
)

clean_rule_en = (
    "[System Rules LitRPG / Isekai]:\n"
    "1. You act as the disembodied Game System and Narrator. Write strictly in third-person perspective.\n"
    "2. No D&D dice rolls (d20) or tabletop commentary.\n"
    "3. Always conclude significant responses with the status panel inside:\n"
    "<stats>\n```\n...\n```\n</stats>"
)

def run_inference(test_title, messages):
    print("\n" + "=" * 80)
    print(f" {test_title}")
    print("=" * 80)
    print(f"• System Prompt: {len(messages[0]['content'])} симв. (Оригинальный English лор)")
    print(f"• first_mes:     {len(messages[1]['content'])} симв. (Чистый English без иероглифов)")
    print(f"• User Input:    «{messages[2]['content'].splitlines()[0]}»")
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
            print("-" * 80)
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
            print("\n" + "-" * 80)
            print(f" МЕТРИКИ: TTFT: {first_token_time:.2f}с | Время: {total_time:.2f}с | Длина: {len(reply_text)} симв.")
    except Exception as e:
        print(f"\n❌ Ошибка вызова: {e}")

# ==========================================
# ТЕСТ 1: РУССКИЙ ВВОД («Привет»)
# ==========================================
anchor_ru = (
    "[Фокус Рассказчика: Дэниэл произносит реплику в пространство. "
    "Опиши отклик окружения и реакцию присутствующих NPC в третьем лице. "
    "Заверши ответ обращением или вопросом NPC к Дэниэлу, передав ему инициативу. "
    "Категорически запрещено отвечать от лица Дэниэла и задавать мета-вопросы от себя («Что будешь делать? / Давай обсудим»). "
    "Обязательно заверши сообщение актуальным блоком характеристик <stats>...</stats>. "
    "Отвечай строго на русском языке.]"
)

messages_test_1 = [
    {"role": "system", "content": f"{pure_en_card_prompt}\n\n{clean_rule_ru}"},
    {"role": "assistant", "content": first_mes_en},
    {"role": "user", "content": f"Привет\n\n{anchor_ru}"}
]

run_inference("ТЕСТ 1: АНГЛИЙСКАЯ КАРТОЧКА + РУССКИЙ ВВОД + ЯКОРЬ ('ОТВЕЧАЙ НА РУССКОМ')", messages_test_1)

# Пауза 2 секунды между прогонами
time.sleep(2)

# ==========================================
# ТЕСТ 2: АНГЛИЙСКИЙ ВВОД («Hello»)
# ==========================================
anchor_en = (
    "[Narrator Focus: Daniel speaks out loud to the room. "
    "Describe the environment and the reaction of the surrounding NPCs in third person. "
    "Conclude your response with an NPC addressing or asking a question to Daniel, passing the initiative to him. "
    "It is strictly forbidden to speak, think, or act on behalf of Daniel, and do not ask out-of-character questions. "
    "Conclude the response with the current status block <stats>...</stats>. "
    "Respond strictly in English.]"
)

messages_test_2 = [
    {"role": "system", "content": f"{pure_en_card_prompt}\n\n{clean_rule_en}"},
    {"role": "assistant", "content": first_mes_en},
    {"role": "user", "content": f"Hello\n\n{anchor_en}"}
]

run_inference("ТЕСТ 2: АНГЛИЙСКАЯ КАРТОЧКА + АНГЛИЙСКИЙ ВВОД + АНГЛИЙСКИЙ ЯКОРЬ", messages_test_2)
