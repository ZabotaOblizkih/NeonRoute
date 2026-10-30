import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  СНЯТИЕ СЛЕПКА: _append_message И СТРИМИНГ ТОКЕНОВ В ЧАТ")
print("=" * 75)

# 1. Тело метода _append_message в chat_flow_controller.py
print("\n[1] Реализация _append_message в ui/controllers/chat_flow_controller.py:")
cf_path = Path("ui/controllers/chat_flow_controller.py")
if cf_path.exists():
    lines = cf_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    in_method = False
    count = 0
    for idx, l in enumerate(lines):
        if "def _append_message(" in l:
            in_method = True
        if in_method:
            print(f"  {lines[idx]}")
            count += 1
            if count > 40:
                break

# 2. Ищем, как стримятся токены в режиме инженер (on_chunk, stream_chunk)
print("\n[2] Обработка чанков стриминга инженера в engineer_controller.py:")
eng_path = Path("ui/controllers/engineer_controller.py")
if eng_path.exists():
    lines = eng_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        low = l.lower()
        if any(k in low for k in ["chunk", "stream", "on_token", "_on_llm_stream", "delta"]):
            if "def " in low or "connect" in low or "emit" in low:
                print(f"  Строка {idx+1}: {l.strip()[:100]}")

print("=" * 75)
