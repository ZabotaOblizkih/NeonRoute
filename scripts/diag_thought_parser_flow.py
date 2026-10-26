import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

tsp_path = root_dir / "services" / "thought_stream_parser.py"
print("=" * 70)
print("1. ИСХОДНЫЙ КОД services/thought_stream_parser.py")
print("=" * 70)
if tsp_path.exists():
    for i, line in enumerate(tsp_path.read_text(encoding="utf-8", errors="ignore").splitlines()):
        print(f"{i+1:03d}: {line}")
else:
    print("Файл services/thought_stream_parser.py не найден!")

print("\n" + "=" * 70)
print("2. ПОШАГОВЫЙ ТРЕЙС РАСПРЕДЕЛЕНИЯ ЧАНКОВ В ПОТОКЕ")
print("=" * 70)
from services.thought_stream_parser import ThoughtStreamParser

# Проверяем типовой ответ модели Qwen2.5-VL
sample_stream = [
    "<think>\n",
    "Пользователь спрашивает про NeonRoute.\n",
    "Нужно ответить четко и по делу.\n",
    "</think>\n\n",
    "NeonRoute — это локальный оркестратор моделей."
]

parser = ThoughtStreamParser()
thought_received = []
normal_received = []

for idx, chunk in enumerate(sample_stream):
    events = parser.feed(chunk)
    print(f"\n[Входящий чанк {idx}]: {repr(chunk)}")
    for ev_type, ev_data in events:
        print(f"   -> EVENT: {ev_type:<14} | DATA: {repr(ev_data)}")
        if ev_type == "thought_chunk":
            thought_received.append(ev_data)
        elif ev_type == "normal_chunk":
            normal_received.append(ev_data)

rem = parser.finalize()
for ev_type, ev_data in rem:
    print(f"   -> [FINALIZE] EVENT: {ev_type:<14} | DATA: {repr(ev_data)}")
    if ev_type == "thought_chunk":
        thought_received.append(ev_data)
    elif ev_type == "normal_chunk":
        normal_received.append(ev_data)

print("\n" + "-" * 70)
print(f"Итого собрано в THOUGHT: {repr(''.join(thought_received))}")
print(f"Итого собрано в NORMAL:  {repr(''.join(normal_received))}")
print("=" * 70)
