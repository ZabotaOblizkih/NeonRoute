import json
import urllib.request
import re
from pathlib import Path

# 1. Читаем карточку напрямую через встроенный json
card_path = Path("characters/Isekai RPG.json")
if not card_path.exists():
    print(f"[-] Файл {card_path} не найден!")
    exit(1)

with open(card_path, "r", encoding="utf-8") as f:
    raw_data = json.load(f)

# Tavern v2 / v1 извлечение first_mes
data_block = raw_data.get("data", raw_data)
first_mes = data_block.get("first_mes", "")

# Берем именно ту вводную сцену (до блока характеристик), где происходят убийство Танаки и появление Сион
# Это около 1500 символов английского текста
scene_text = first_mes
if "<stats>" in scene_text:
    scene_text = scene_text.split("<stats>")[0]
elif "**Physical Status:**" in scene_text:
    scene_text = scene_text.split("**Physical Status:**")[0]
elif "Physical status:" in scene_text.lower():
    idx = scene_text.lower().find("physical status:")
    scene_text = scene_text[:idx]

print("=== 1. ИСХОДНЫЙ ТЕКСТ СЦЕНЫ (АНГЛИЙСКИЙ ОРИГИНАЛ) ===")
print(scene_text.strip())
print(f"\nДлина: {len(scene_text)} символов")

# 2. Чистый системный промпт без перегрузки правилами и без негативных примеров
system_prompt = (
    "Ты — профессиональный писатель и литературный переводчик на русский язык.\n"
    "Твоя задача: перевести фрагмент художественного текста на выразительный, богатый русский язык.\n"
    "Переводи полностью, сохраняя прямую речь, диалоги и абзацы.\n"
    "В выводе должен быть исключительно готовый перевод на русском языке без комментариев."
)

user_prompt = f"Переведи следующий текст на русский язык:\n\n{scene_text.strip()}"

payload = {
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    "temperature": 0.3,
    "top_p": 0.9,
    "max_tokens": 2048,
    "stream": False
}

print("\n=== 2. ОТПРАВКА ПРЯМОГО ЗАПРОСА В LLAMA-SERVER (127.0.0.1:1234) ===")
req = urllib.request.Request(
    "http://127.0.0.1:1234/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        raw_output = res_data["choices"][0]["message"]["content"]
        
        print("\n=== 3. СЫРОЙ ОТВЕТ МОДЕЛИ БЕЗ ОБРАБОТКИ КОДОМ ===")
        print(raw_output.strip())
        
        print("\n=== 4. АНАЛИЗ ТОКЕНОВ В СЫРОМ ОТВЕТЕ ===")
        # Проверяем наличие смешанных слов (латиница + кириллица)
        mixed = re.findall(r'\b(?=[a-zA-Z]*[а-яА-ЯёЁ])(?=[а-яА-ЯёЁ]*[a-zA-Z])[a-zA-Zа-яА-ЯёЁ]{2,}\b', raw_output)
        print(f"Смешанные слова: {mixed if mixed else 'НЕТ (чистый текст)'}")
        
        # Проверяем ключевые фразы
        lines = raw_output.splitlines()
        sage_lines = [l.strip() for l in lines if any(w in l.lower() for w in ("мудрец", "sage", "сион"))]
        print(f"\nСтроки с упоминанием Сион / Мудреца:")
        for l in sage_lines[:3]:
            print(f"  • {l}")
            
        scream_lines = [l.strip() for l in lines if any(w in l.lower() for w in ("крик", "паник", "танака", "scream"))]
        print(f"\nСтроки с реакцией на убийство Танаки:")
        for l in scream_lines[:3]:
            print(f"  • {l}")

except Exception as e:
    print(f"[-] Ошибка выполнения прямого запроса: {e}")
