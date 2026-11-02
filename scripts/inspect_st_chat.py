import json
from pathlib import Path

# 1. Читаем файл чата SillyTavern
chat_path = Path(r"C:\neyroGPD\SillyTavern-release\data\default-user\chats\Кай и Эйла\Кай и Эйла - 2026-10-31@15h54m06s644ms.jsonl")

print("=" * 80)
print(f" АНАЛИЗ ЧАТА SILLYTAVERN: {chat_path.name}")
print("=" * 80)

if chat_path.exists():
    lines = [line.strip() for line in chat_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Всего строк/записей в файле: {len(lines)}")
    
    # Первая строка — метаданные чата
    header = json.loads(lines[0])
    print("\n[МЕТАДАННЫЕ ЧАТА (Строка 0)]:")
    print(f"• Имя персонажа:  {header.get('character_name') or header.get('name')}")
    print(f"• Имя пользователя: {header.get('user_name')}")
    print(f"• Пресет/модель:    {header.get('model') or header.get('preset')}")
    if "extra" in header:
        print(f"• Доп. поля header: {list(header['extra'].keys())}")

    # Последнее сообщение ассистента
    last_msg = json.loads(lines[-1])
    print("\n[ПОСЛЕДНЕЕ СООБЩЕНИЕ В ЧАТЕ]:")
    print(f"• От кого: {last_msg.get('name')} (is_user={last_msg.get('is_user')})")
    print(f"• Текст:\n{last_msg.get('mes')}")
    
    # Проверяем, сохранила ли Таверна сырой промпт внутри сообщения
    extra = last_msg.get("extra", {})
    if "prompt" in extra or "gen_prompt" in extra:
        print("\n[СЫРОЙ ПРОМПТ ИЗ EXTRA]:")
        print(extra.get("prompt") or extra.get("gen_prompt"))
else:
    print(f"❌ Файл {chat_path} не найден.")

# 2. Ищем логи самого сервера LM Studio
print("\n" + "=" * 80)
print(" ПОИСК ЗАПРОСА В ЛОГАХ LM STUDIO")
print("=" * 80)

user_home = Path.home()
possible_log_dirs = [
    user_home / ".cache" / "lm-studio" / "logs",
    user_home / "AppData" / "Roaming" / "LM Studio" / "logs",
    user_home / "AppData" / "Local" / "LM Studio" / "logs"
]

found_logs = []
for p in possible_log_dirs:
    if p.exists():
        found_logs.extend(p.glob("*.log"))

if found_logs:
    print(f"Найдено лог-файлов LM Studio: {len(found_logs)}")
    for lf in sorted(found_logs, key=lambda x: x.stat().st_mtime, reverse=True)[:3]:
        print(f"• Проверяем: {lf.name} ({lf.stat().st_size} байт)")
        try:
            txt = lf.read_text(encoding="utf-8", errors="ignore")
            if "16:04" in txt or "meissa-qwen2.5-7b-instruct" in txt or "/chat/completions" in txt:
                print(f"  👉 Найдено совпадение в {lf.name}!")
                # Ищем куски с chat/completions
                for chunk in txt.split("POST /v1/chat/completions")[-2:]:
                    snippet = chunk[:1200]
                    print(f"--- Фрагмент лога ---\n...{snippet}\n---------------------")
        except Exception as e:
            print(f"  Ошибка чтения {lf.name}: {e}")
else:
    print("Лог-директории LM Studio не найдены в стандартных путях.")
print("=" * 80)
