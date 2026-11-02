import json
import re
from pathlib import Path

log_path = Path(r"C:\Users\user\.lmstudio\server-logs\2026-10\2026-10-31.1.log")

if not log_path.exists():
    print(f"❌ Файл {log_path} не найден.")
    exit(1)

content = log_path.read_text(encoding="utf-8", errors="ignore")

print("=" * 80)
print(f" АНАЛИЗ ЗАПРОСОВ SILLYTAVERN ИЗ ЛОГА LM STUDIO: {log_path.name}")
print("=" * 80)

# Ищем блоки с /chat/completions и JSON-телом
# Обычно LM Studio логирует "POST /v1/chat/completions" или тело с "messages": [...]
matches = []

# Поиск всех JSON-подобных блоков с "messages"
pattern = re.compile(r'(\{[\s\S]*?"messages"\s*:\s*\[[\s\S]*?\}(?=\n\S|\Z))')
for m in pattern.finditer(content):
    matches.append(m.group(1))

if not matches:
    # Попробуем разделить по POST /v1/chat/completions
    parts = content.split("/v1/chat/completions")
    if len(parts) > 1:
        print(f"Найдено обращений к /v1/chat/completions: {len(parts) - 1}")
        last_part = parts[-1]
        print("\n--- Хвост последнего обращения ---")
        print(last_part[:3000])
    else:
        print("Вхождений /v1/chat/completions не найдено. Выводим последние 40 строк лога:")
        print("\n".join(content.splitlines()[-40:]))
    exit(0)

print(f"Найдено структурированных запросов с messages: {len(matches)}")
last_payload_raw = matches[-1]

try:
    payload = json.loads(last_payload_raw)
except Exception:
    # Если захватили лишнее, ищем границы валидного JSON
    brace_cnt = 0
    valid_json_str = ""
    for i, ch in enumerate(last_payload_raw):
        if ch == '{':
            brace_cnt += 1
        elif ch == '}':
            brace_cnt -= 1
            if brace_cnt == 0:
                valid_json_str = last_payload_raw[:i+1]
                break
    try:
        payload = json.loads(valid_json_str)
    except Exception:
        payload = None

if payload:
    msgs = payload.get("messages", [])
    print(f"\n[ПАРАМЕТРЫ ЗАПРОСА SILLYTAVERN]:")
    for k in ["model", "temperature", "top_p", "min_p", "repetition_penalty", "max_tokens"]:
        if k in payload:
            print(f"• {k}: {payload[k]}")

    print(f"\n[ВСЕГО СООБЩЕНИЙ В ПАКЕТЕ: {len(msgs)}]")
    for idx, m in enumerate(msgs):
        role = m.get("role", "unknown").upper()
        txt = m.get("content", "")
        print(f"\n--- [{idx}] ROLE: {role} (Длина: {len(txt)} симв.) ---")
        if role == "SYSTEM":
            print(txt)
        elif len(txt) > 300:
            print(txt[:150] + "\n... [пропуск] ...\n" + txt[-150:])
        else:
            print(txt)
else:
    print("Не удалось распарсить чистый JSON, сырой фрагмент:")
    print(last_payload_raw[:2500])

print("=" * 80)
