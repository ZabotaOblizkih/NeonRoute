import re
from pathlib import Path

af_path = Path("ui/controllers/assistant_flow_controller.py")
cf_path = Path("ui/controllers/chat_flow_controller.py")

af_text = af_path.read_text(encoding="utf-8", errors="ignore")
cf_text = cf_path.read_text(encoding="utf-8", errors="ignore")

print("=" * 70)
print("АНАЛИЗ ЖИЗНЕННОГО ЦИКЛА _thought_stream_parser И text В КОНТРОЛЛЕРАХ")
print("=" * 70)

print("\n[1] Создание _thought_stream_parser:")
for path_name, text in [("assistant_flow_controller", af_text), ("chat_flow_controller", cf_text)]:
    for i, line in enumerate(text.splitlines()):
        if "_thought_stream_parser =" in line or "_thought_stream_parser=" in line:
            print(f"   {path_name}:{i+1} -> {line.strip()}")

print("\n[2] Зануление / финализация _thought_stream_parser:")
for path_name, text in [("assistant_flow_controller", af_text), ("chat_flow_controller", cf_text)]:
    for i, line in enumerate(text.splitlines()):
        if "_thought_stream_parser" in line and ("None" in line or "finalize" in line):
            print(f"   {path_name}:{i+1} -> {line.strip()}")

print("\n[3] Блоки с проверкой 'if not text' в assistant_flow_controller.py:")
for i, line in enumerate(af_text.splitlines()):
    if "if not text" in line:
        start = max(0, i - 5)
        end = min(len(af_text.splitlines()), i + 15)
        print(f"\n--- Строки {start+1}-{end+1} ---")
        for idx in range(start, end):
            print(f"{idx+1:04d}: {af_text.splitlines()[idx]}")

print("=" * 70)
