import sys
import json
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ПРОВЕРКА БОЕВОГО КОНТЕКСТА: ЧТО МОДЕЛЬ ВИДИТ В MESSAGES")
print("=" * 80)

# Берем последнюю сессию
sess_dir = project_root / "outputs" / "sessions" / "e6888a72-e18c-4c32-a202-24d6f2d541c2"
sess_file = sess_dir / "session.json"

if sess_file.exists():
    sess_data = json.loads(sess_file.read_text(encoding="utf-8"))
    raw_msgs = sess_data.get("messages", [])
    print(f"Всего сырых сообщений в файле сессии: {len(raw_msgs)}")
    
    # Эмулируем маппинг сообщений, как это делает контроллер
    mapped_messages = []
    for idx, m in enumerate(raw_msgs):
        is_user = m.get("is_user", False)
        role = "user" if is_user else "assistant"
        text = m.get("text", "")
        mapped_messages.append({"role": role, "content": text})
        print(f"[{idx}] role: {role:<10} | Длина: {len(text):<5} симв. | Начало: {text[:80].replace(chr(10), ' ')}...")
else:
    print(f"Файл {sess_file} не найден")

print("=" * 80)
