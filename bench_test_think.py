import json
import requests
import time
from services.thought_stream_parser import ThoughtStreamParser, ThinkParserState
from services.assistant_prompt_builder import AssistantPromptBuilder

LMS_URL = "http://127.0.0.1:1234/v1/chat/completions"
MODEL_NAME = "Qwen2.5-VL-7B-Instruct-GGUF"

print("=" * 70)
print("ТЕСТОВЫЙ СТЕНД: ПРОВЕРКА РЕАКЦИИ МОДЕЛИ И ПАРСИНГА <think>")
print("=" * 70)

# Вариант А: Текущий хвост v13 (из кода)
tail_current = AssistantPromptBuilder.ROUTING_TAIL_PROTOCOL

# Вариант Б: Визуальный скелет-шаблон
tail_skeleton = (
    "[СТРОГИЙ ПОРЯДОК ГЕНЕРАЦИИ]\n"
    "Каждый ответ формируется СТРОГО по шаблону с обязательным закрытием тега </think>:\n\n"
    "<think>\n"
    "- Суть запроса: (краткий анализ: приветствие / вопрос по коду / галерея)\n"
    "</think>\n"
    "(Здесь изложи чистовой ответ строго на «ты»)\n\n"
    "Правила:\n"
    "1. Тег </think> закрывается ВСЕГДА до начала реплики собеседнику.\n"
    "2. После </think> пиши живой ответ."
)

user_msg = "Привет"

variants = [
    ("ТЕКУЩИЙ ПРОТОКОЛ v13", tail_current),
    ("НОВЫЙ ВИЗУАЛЬНЫЙ СКЕЛЕТ", tail_skeleton)
]

for label, tail in variants:
    print(f"\n>>> ТЕСТИРУЕМ: {label}")
    sys_prompt = AssistantPromptBuilder.build(
        user_name="Виктор",
        active_persona={"bio": "Разработчик NeonRoute"},
        include_memory=False
    )
    # Подменяем хвост для чистоты эксперимента
    sys_prompt = sys_prompt.replace(AssistantPromptBuilder.ROUTING_TAIL_PROTOCOL, tail)

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg}
        ],
        "temperature": 0.8,
        "min_p": 0.05,
        "max_tokens": 512,
        "stream": True
    }

    try:
        resp = requests.post(LMS_URL, json=payload, stream=True, timeout=15)
        if resp.status_code != 200:
            print(f"Ошибка HTTP {resp.status_code}: {resp.text}")
            continue

        parser = ThoughtStreamParser()
        raw_full_text = ""

        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8")
            if line_str.startswith("data: "):
                data_part = line_str[6:].strip()
                if data_part == "[DONE]":
                    break
                try:
                    chunk_obj = json.loads(data_part)
                    delta = chunk_obj["choices"][0]["delta"].get("content", "")
                    if delta:
                        raw_full_text += delta
                        parser.feed(delta)
                except Exception:
                    pass

        parser.finalize()

        print(f"\n[1. СЫРОЙ ВЫВОД МОДЕЛИ ({len(raw_full_text)} симв.)]:")
        print(repr(raw_full_text))

        has_open = "<think>" in raw_full_text.lower()
        has_close = "</think>" in raw_full_text.lower()
        print(f"\n[2. СТАТУС ТЕГОВ]: <think>={has_open} | </think>={has_close}")

        print(f"\n[3. РЕЗУЛЬТАТ ПАРСЕРА]:")
        print(f"  Мысли (Thought): {repr(parser.thought_text)}")
        print(f"  Ответ (Normal):  {repr(parser.normal_text)}")

        if not parser.normal_text.strip():
            print("  ⚠️ ВНИМАНИЕ: Чистовой ответ ПУСТОЙ! (Сработала бы аварийная заглушка)")
        else:
            print("  ✅ УСПЕХ: Чистовой ответ успешно отделен и готов к показу в чате.")

    except Exception as e:
        print(f"Ошибка запроса к серверу: {e}")

print("\n" + "=" * 70)
print("ТЕСТ ЗАВЕРШЕН")
print("=" * 70)
