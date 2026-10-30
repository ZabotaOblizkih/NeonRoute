import sys
import json
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=== 1. ИЗУЧАЕМ СЕССИЮ ИЗ ЛОГА (abf6f6fb...) ===")
sess_id = "abf6f6fb-f96e-4e5b-8d93-c32221ec238d"
sess_file = None
for p in Path(".").rglob(f"*{sess_id}*.json"):
    sess_file = p
    break

if sess_file and sess_file.exists():
    print(f"Найден файл сессии: {sess_file}")
    data = json.loads(sess_file.read_text(encoding="utf-8"))
    history = data.get("history", [])
    print(f"Всего сообщений в истории: {len(history)}")
    for i, msg in enumerate(history):
        role = msg.get("role")
        txt = msg.get("content", "")
        if isinstance(txt, list):
            txt = " ".join(x.get("text", "") for x in txt if isinstance(x, dict))
        print(f"\n--- Сообщение {i} [{role}] (длина {len(txt)} симв.) ---")
        print(txt[:300] + ("..." if len(txt) > 300 else ""))
else:
    print(f"Файл сессии {sess_id} не найден на диске, ищем последние сессии в sessions/:")
    for p in sorted(Path(".").rglob("*.json")):
        if "sessions" in p.parts:
            print(f"  {p}")

print("\n" + "=" * 80)
print("=== 2. КАК ContextWindowBuilder.get_lm_studio_messages СТРОИТ MESSAGES ===")
print("=" * 80)
p_cwb = Path("services/context_window_builder.py")
lines = p_cwb.read_text(encoding="utf-8").splitlines()
for i, l in enumerate(lines):
    if "def get_lm_studio_messages(" in l:
        for j in range(i, min(len(lines), i + 65)):
            print(f"{j+1:4d}: {lines[j]}")
        break
