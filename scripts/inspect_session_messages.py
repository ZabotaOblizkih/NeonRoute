import sys
import json
import re
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" РЕНТГЕН ИСТОРИИ СЕССИИ И ПОДГОТОВКИ MESSAGES ДЛЯ LLM")
print("=" * 80)

# 1. Смотрим структуру последнего session.json
session_file = project_root / "outputs" / "sessions" / "e6888a72-e18c-4c32-a202-24d6f2d541c2" / "session.json"
if session_file.exists():
    try:
        raw_data = json.loads(session_file.read_text(encoding="utf-8"))
        print(f"[1. СТРУКТУРА КЛЮЧЕЙ В session.json]: {list(raw_data.keys()) if isinstance(raw_data, dict) else 'list'}")
        
        # Ищем список сообщений
        msgs = raw_data.get("messages") or raw_data.get("history") or []
        print(f"Всего записей в истории: {len(msgs)}")
        if msgs:
            print("\nПервая запись (индекс 0) целиком:")
            print(json.dumps(msgs[0], ensure_ascii=False, indent=2))
            if len(msgs) > 1:
                print("\nВторая запись (индекс 1) целиком:")
                print(json.dumps(msgs[1], ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"Ошибка чтения session.json: {e}")
else:
    print(f"Файл {session_file} не найден")

# 2. Как roleplay_flow_controller формирует messages перед отправкой в LLM
flow_file = project_root / "services" / "roleplay_flow_controller.py"
if flow_file.exists():
    f_content = flow_file.read_text(encoding="utf-8")
    print("\n" + "=" * 80)
    print("[2. ФОРМИРОВАНИЕ СПИСКА MESSAGES В roleplay_flow_controller.py]:")
    
    # Ищем блок, где собираются messages = [...]
    matches = re.findall(r'(#?[^\n]*messages\s*=\s*\[[\s\S]*?\])', f_content)
    if matches:
        for m in matches[:3]:
            print("\n--- Найден блок сборки messages: ---")
            print(m[:1000])
    else:
        # Ищем цикл по истории
        loop_match = re.search(r'(for\s+\w+\s+in\s+.*(?:history|messages)[\s\S]*?append[\s\S]*?)(?=\n\s{4,8}[a-zA-Z_]+\s*=|\Z)', f_content)
        if loop_match:
            print("\n--- Цикл обработки сообщений: ---")
            print(loop_match.group(1)[:1000])

# 3. Как session_manager инициализирует новую сессию карточки
sm_file = project_root / "services" / "session_manager.py"
if sm_file.exists():
    sm_content = sm_file.read_text(encoding="utf-8")
    print("\n" + "=" * 80)
    print("[3. ИНИЦИАЛИЗАЦИЯ ПЕРВОГО СООБЩЕНИЯ В session_manager.py]:")
    
    # Ищем create_session или init_session
    init_match = re.search(r'def\s+(?:create_session|new_session|init_session|load_or_create)[\s\S]*?(?=\ndef |\Z)', sm_content)
    if init_match:
        lines = [l for l in init_match.group(0).splitlines() if any(k in l for k in ["first_mes", "greeting", "messages", "append", "history"])]
        for l in lines[:20]:
            print(f"  {l}")

print("\n" + "=" * 80)
