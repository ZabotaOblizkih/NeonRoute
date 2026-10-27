import json
from pathlib import Path

target_path = Path("characters/Isekai RPG.test_translated.json")
if not target_path.exists():
    print(f"[!] Файл не найден: {target_path}")
    exit(1)

with open(target_path, "r", encoding="utf-8") as f:
    raw = json.load(f)

data = raw.get("data", raw)

def print_section(title, text, max_len=500):
    text_clean = (text or "").strip()
    length = len(text_clean)
    print("\n" + "=" * 70)
    print(f"  {title} (Длина: {length} симв.)")
    print("=" * 70)
    if not text_clean:
        print("[ПУСТО]")
        return
    if length > max_len:
        print(text_clean[:max_len])
        print(f"\n... [Обрезано. Показано {max_len} из {length} симв.] ...")
    else:
        print(text_clean)

# 1. Проверка скалярных полей
print_section("DESCRIPTION_RU", data.get("description_ru", ""))
print_section("SCENARIO_RU", data.get("scenario_ru", ""))
print_section("FIRST_MES_RU", data.get("first_mes_ru", ""), max_len=600)

# 2. Детальный аудит PERSONALITY_RU (почему вырос до 14k)
pers = data.get("personality_ru", "") or ""
print_section("PERSONALITY_RU (Начало)", pers, max_len=600)
if len(pers) > 1200:
    mid = len(pers) // 2
    print_section("PERSONALITY_RU (Середина)", pers[mid : mid + 600], max_len=600)
    print_section("PERSONALITY_RU (Конец)", pers[-600:], max_len=600)

# 3. Альтернативные приветствия
alts = data.get("alternate_greetings_ru", []) or []
print("\n" + "=" * 70)
print(f"  ALTERNATE_GREETINGS_RU (Всего элементов: {len(alts)})")
print("=" * 70)
for idx, alt in enumerate(alts, 1):
    print(f"\n--- Приветствие #{idx} (Длина: {len(alt)} симв.) ---")
    print(alt[:350].strip() + ("..." if len(alt) > 350 else ""))

# 4. Проверка целостности тегов и артефактов
all_text = " ".join([
    data.get("description_ru", "") or "",
    data.get("personality_ru", "") or "",
    data.get("first_mes_ru", "") or "",
    " ".join(alts)
])

unmasked_count = all_text.count("__NR_TAG_")
user_tags = all_text.count("{{user}}")
char_tags = all_text.count("{{char}}")

print("\n" + "=" * 70)
print("  СВОДКА ПО ТЕГАМ И АРТЕФАКТАМ")
print("=" * 70)
print(f"Неразмаскированные теги (__NR_TAG_X__) : {unmasked_count}  (должно быть 0)")
print(f"Макросы {{user}}                       : {user_tags}")
print(f"Макросы {{char}}                       : {char_tags}")
print("=" * 70)
