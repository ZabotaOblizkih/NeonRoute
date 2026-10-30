import sys
import json
import re
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ДИАГНОСТИКА: ПЕРВОЕ СООБЩЕНИЕ (FIRST_MES) В СЕССИЯХ NEONROUTE")
print("=" * 80)

# 1. Анализ кода: как обрабатывается first_mes при создании чата
files_to_check = [
    project_root / "services" / "session_manager.py",
    project_root / "services" / "roleplay_flow_controller.py",
    project_root / "services" / "roleplay_prompt_builder.py"
]

print("\n[1. ПОИСК ОБРАБОТКИ first_mes В КОДОВОЙ БАЗЕ]:")
for file_path in files_to_check:
    if file_path.exists():
        text = file_path.read_text(encoding="utf-8")
        matches = [line.strip() for line in text.splitlines() if "first_mes" in line]
        print(f"\n📄 {file_path.name} (найдено строк: {len(matches)}):")
        for m in matches[:10]:
            print(f"   • {m}")
    else:
        print(f"❌ {file_path.name} не найден")

# 2. Проверка реальных файлов сессий на диске
print("\n" + "=" * 80)
print("[2. АНАЛИЗ ПОСЛЕДНЕЙ АКТИВНОЙ СЕССИИ НА ДИСКЕ]:")

# Ищем папки с сессиями
possible_dirs = [
    project_root / "history",
    project_root / "sessions",
    project_root / "outputs" / "sessions",
    project_root / "data" / "sessions"
]

chat_files = []
for p_dir in possible_dirs:
    if p_dir.exists():
        found = list(p_dir.glob("**/*.json"))
        chat_files.extend(found)

if not chat_files:
    # Ищем вообще любые chat_*.json в проекте
    chat_files = list(project_root.glob("**/chat_*.json"))

if chat_files:
    # Сортируем по времени модификации, берем самый свежий
    latest_chat = max(chat_files, key=lambda f: f.stat().st_mtime)
    print(f"• Последний активный файл чата: {latest_chat.relative_to(project_root)}")
    
    try:
        data = json.loads(latest_chat.read_text(encoding="utf-8"))
        messages = data if isinstance(data, list) else data.get("messages", data.get("history", []))
        
        print(f"• Всего сообщений в истории: {len(messages)}")
        if messages:
            print("\nПервые 3 сообщения в файле сессии:")
            for i, msg in enumerate(messages[:3]):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                preview = content[:150].replace("\n", " ")
                print(f"  [{i}] Роль: {role:<10} | Текст: {preview}...")
        else:
            print("⚠️ Массив сообщений пуст!")
    except Exception as e:
        print(f"❌ Ошибка чтения файла чата: {e}")
else:
    print("⚠️ Файлы сессий (.json) не обнаружены.")

print("\n" + "=" * 80)
