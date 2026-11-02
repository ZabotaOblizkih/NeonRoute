import json
import os
from datetime import datetime, timedelta
from pathlib import Path

chat_file = Path(r"C:\neyroGPD\SillyTavern-release\data\default-user\chats\Кай и Эйла\Кай и Эйла - 2026-10-31@15h54m06s644ms.jsonl")

print("=" * 80)
print("1. ПРОВЕРКА ПОЛЕЙ EXTRA В СООБЩЕНИЯХ SILLYTAVERN")
print("=" * 80)

if chat_file.exists():
    lines = [json.loads(line) for line in chat_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    for idx, msg in enumerate(lines):
        extra = msg.get("extra", {})
        sender = msg.get("name", "Unknown")
        print(f"[{idx}] {sender} | extra keys: {list(extra.keys())}")
        # Если есть поля с промптом или генерацией
        for k in ["prompt", "gen_prompt", "raw_prompt", "token_count", "model"]:
            if k in extra:
                print(f"    -> extra['{k}']: {extra[k]}")
else:
    print("Файл чата не найден.")

print("\n" + "=" * 80)
print("2. ПОИСК СВЕЖИХ ЛОГОВ (ИЗМЕНЕННЫХ ЗА ПОСЛЕДНИЕ 2 ЧАСА)")
print("=" * 80)

cutoff = datetime.now() - timedelta(hours=3)
search_roots = [
    Path(r"C:\neyroGPD\SillyTavern-release"),
    Path.home() / "AppData" / "Local" / "LM Studio",
    Path.home() / "AppData" / "Roaming" / "LM Studio",
    Path.home() / ".cache" / "lm-studio",
    Path.home() / ".lmstudio"
]

found_recent = []
for root in search_roots:
    if not root.exists():
        continue
    for p in root.rglob("*.log"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime > cutoff:
                found_recent.append((p, mtime, p.stat().st_size))
        except Exception:
            pass

if found_recent:
    for p, mtime, size in sorted(found_recent, key=lambda x: x[1], reverse=True):
        print(f"• {mtime.strftime('%H:%M:%S')} | {size:>8} б | {p}")
else:
    print("Свежих .log файлов за последние часы не обнаружено.")

print("=" * 80)
