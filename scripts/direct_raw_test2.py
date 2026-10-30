import json
import urllib.request
import re
from pathlib import Path

card_path = Path("characters/Isekai RPG.json")
with open(card_path, "r", encoding="utf-8") as f:
    raw_data = json.load(f)

first_mes = raw_data.get("data", raw_data).get("first_mes", "")

# 1. Тот же исходный фрагмент
system_prompt = (
    "Ты — профессиональный писатель и литературный переводчик художественной прозы на русский язык.\n"
    "ПРАВИЛА:\n"
    "1. Переводи выразительным русским языком, сохраняя диалоги, реплики и структуру текста.\n"
    "2. Все титулы, ранги и игровые классы персонажей (даже если они написаны в оригинале с заглавной буквы: Sage, Great Sage, Mage) переводи смысловыми русскими словами (Мудрец, Великий Мудрец, Маг), а не транслитом или латиницей.\n"
    "3. Выводи только готовый перевод без комментариев."
)

payload = {
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Переведи следующий текст на русский язык:\n\n{first_mes.strip()}"}
    ],
    "temperature": 0.25,
    "top_p": 0.9,
    "max_tokens": 2048,
    "stream": False
}

print("=== ТЕСТ ПРЯМОГО ЗАПРОСА С ПРАВИЛОМ ДЛЯ ТИТУЛОВ И КЛАССОВ ===")
req = urllib.request.Request(
    "http://127.0.0.1:1234/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        raw_output = res_data["choices"][0]["message"]["content"]
        
        print("\n=== ВЫВОД ПЕРЕВОДА СЦЕНЫ ===")
        # Выводим первые 1200 символов для оценки
        print(raw_output[:1200] + "\n... [продолжение следует]")
        
        mixed = re.findall(r'\b(?=[a-zA-Z]*[а-яА-ЯёЁ])(?=[а-яА-ЯёЁ]*[a-zA-Z])[a-zA-Zа-яА-ЯёЁ]{2,}\b', raw_output)
        print("\n=== ПРОВЕРКА ТОКЕНОВ ===")
        print(f"Смешанные слова: {mixed if mixed else 'НЕТ (абсолютно чистый перевод!)'}")
        
        print("\nУпоминания Мудреца в переводе:")
        for l in raw_output.splitlines():
            if any(w in l.lower() for w in ("мудрец", "sage", "сион")):
                print(f"  • {l.strip()}")

except Exception as e:
    print(f"[-] Ошибка: {e}")
