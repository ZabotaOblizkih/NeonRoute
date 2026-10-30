import sys
import json
import time
import urllib.request
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ТЕСТОВЫЙ СТЕНД: ЧИСТЫЙ ВЫЗОВ LLM С ПОЛНЫМ СТЕКОМ СЭМПЛЕРОВ")
print("=" * 80)

# 1. Загрузка оригинальной карточки без изменений
from services.tavern_parser import TavernParser
from services.context_window_builder import ContextWindowBuilder

card_path = project_root / "characters" / "Isekai RPG.json"
card = TavernParser.parse_file(str(card_path))

# 2. Ввод реплики
print("Введи свой точный текст, который ты отправлял модели (или нажми Enter для «Статус»):")
user_query = input("Реплика игрока > ").strip()
if not user_query:
    user_query = "Статус"

# Имя персонажа игрока (если в профиле был Дэниэл или Игрок)
user_name_input = input("Имя игрока [по умолчанию 'Игрок']: ").strip()
user_name = user_name_input if user_name_input else "Игрок"

# 3. Штатная сборка системного промпта
sys_prompt = ContextWindowBuilder.build_system_prompt(
    character_card=card,
    user_name=user_name,
    is_roleplay=True,
    is_engineer=False
)

first_mes = getattr(card, "first_mes_ru", None) or getattr(card, "first_mes", "") or ""

# 4. Формирование точного массива messages
messages = [
    {"role": "system", "content": sys_prompt},
    {"role": "assistant", "content": first_mes},
    {"role": "user", "content": user_query}
]

print(f"\n[Контекст] Системный промпт: {len(sys_prompt)} симв. | first_mes: {len(first_mes)} симв.")
print(f"[Контекст] Игрок ({user_name}): «{user_query}»")

# 5. Полный боевой payload со всеми параметрами инференса NeonRoute
payload = {
    "model": "Qwen2.5-VL-7B-Instruct-GGUF",
    "messages": messages,
    "temperature": 1.0,
    "top_p": 0.95,
    "min_p": 0.05,
    "repetition_penalty": 1.02,
    "repeat_penalty": 1.02,
    # DRY сэмплер (подавление циклических повторов)
    "dry_multiplier": 0.8,
    "dry_base": 1.75,
    "dry_allowed_length": 2,
    "dry_penalty_last_n": 0,
    # XTC сэмплер (исключение тривиальных токенов)
    "xtc_probability": 0.3,
    "xtc_threshold": 0.1,
    # Параметры буфера и кэша
    "cache_prompt": True,
    "max_tokens": 4096,
    "max_completion_tokens": 4096,
    "stream": True
}

target_url = "http://127.0.0.1:1234/v1/chat/completions"
print(f"[Инференс] Отправка запроса в {target_url}...")

req = urllib.request.Request(
    target_url,
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
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
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
        print(" МЕТРИКИ:")
        print(f"  • TTFT: {first_token_time:.2f} с" if first_token_time else "  • TTFT: N/A")
        print(f"  • Время генерации: {total_time:.2f} с")
        print(f"  • Длина ответа: {len(reply_text)} симв.")
        print("=" * 80)

except urllib.error.URLError as e:
    print(f"\n❌ Ошибка подключения к серверу: {e}")
    print("Убедись, что llama-server запущен и слушает порт 1234.")
