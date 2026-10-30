import json
import urllib.request
import re
from pathlib import Path

card_path = Path("characters/Isekai RPG.json")
if not card_path.exists():
    print(f"[-] Файл {card_path} не найден!")
    exit(1)

with open(card_path, "r", encoding="utf-8") as f:
    raw_data = json.load(f)

data_block = raw_data.get("data", raw_data)
first_mes = data_block.get("first_mes", "")

system_prompt = (
    "Ты — профессиональный писатель и литературный переводчик художественной прозы на русский язык.\n"
    "ПРАВИЛА:\n"
    "1. Переводи выразительным русским литературным языком, сохраняя диалоги, реплики и структуру текста.\n"
    "2. Все титулы, ранги, статусы и игровые классы персонажей (даже если они написаны в оригинале с заглавной буквы: Sage, Great Sage, Mage) переводи смысловыми русскими словами (Мудрец, Великий Мудрец, Маг), а не транслитом или латиницей.\n"
    "3. Названия характеристик и параметров (строки вида Параметр: Значение) переводи на русский язык.\n"
    "4. Выводи только готовый перевод без комментариев."
)

payload = {
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Переведи следующий текст на русский язык:\n\n{first_mes.strip()}"}
    ],
    "temperature": 0.1,
    "top_p": 0.9,
    "max_tokens": 2048,
    "stream": False
}

print("=== ПРЯМОЙ ТЕСТ: FIRST_MES ЦЕЛИКОМ (ТЕМПЕРАТУРА 0.1, БЕЗ ЧАНКИНГА И ОБВЯЗОК) ===")
print(f"Размер исходного текста: {len(first_mes)} символов\n")

req = urllib.request.Request(
    "http://127.0.0.1:1234/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        raw_output = res_data["choices"][0]["message"]["content"]

        print("=" * 60)
        print("РЕЗУЛЬТАТ ПРЯМОГО ПЕРЕВОДА:")
        print("=" * 60)
        print(raw_output.strip())
        print("=" * 60)

        mixed = re.findall(r'\b(?=[a-zA-Z]*[а-яА-ЯёЁ])(?=[а-яА-ЯёЁ]*[a-zA-Z])[a-zA-Zа-яА-ЯёЁ]{2,}\b', raw_output)
        cjk = re.findall(r'[\u4e00-\u9fff]', raw_output)

        print("\n=== ВЕРИФИКАЦИЯ ===")
        print(f"Смешанные слова (латиница+кириллица): {mixed if mixed else 'НЕТ (чистый текст)'}")
        print(f"Иероглифы (CJK): {len(cjk)} символов ({''.join(cjk[:10]) if cjk else 'НЕТ (чистый текст)'})")

        print("\nСтроки со словом 'Крик / Screams':")
        for l in raw_output.splitlines():
            if any(w in l.lower() for w in ("крик", "паник", "scream")):
                print(f"  • {l.strip()}")

        print("\nСтроки со словом 'Мудрец / Sage':")
        for l in raw_output.splitlines():
            if any(w in l.lower() for w in ("мудрец", "sage")):
                print(f"  • {l.strip()}")

except Exception as e:
    print(f"[-] Ошибка выполнения запроса: {e}")
