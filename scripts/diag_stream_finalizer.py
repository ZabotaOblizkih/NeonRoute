import re
import sys
from pathlib import Path

# Подключаем модули проекта
sys.path.insert(0, str(Path(__file__).parent.parent))
from services.thought_stream_parser import ThoughtStreamParser

print("=" * 70)
print("СКВОЗНОЙ ТЕСТ: Эмуляция генерации и финализации ответа")
print("=" * 70)

# Имитируем реальный сырой поток токенов от llama-server
chunks = [
    "<th", "ink>\n",
    "Собе", "седник ", "хочет ", "поздоро", "ваться.\n",
    "</th", "ink>\n\n",
    "Привет, ", "Виктор! ", "Как ", "дела?"
]

parser = ThoughtStreamParser()
stream_buffer = ""
emitted_normal_text = ""

print("\n1. Прогон чанков через ThoughtStreamParser:")
for i, ch in enumerate(chunks):
    events = parser.feed(ch)
    for ev_type, ev_data in events:
        if ev_type == "normal_chunk":
            stream_buffer += ev_data
            emitted_normal_text += ev_data
            print(f"   [Чанк {i:02d}] Normal chunk -> '{ev_data}'")
        elif ev_type == "thought_chunk":
            pass
        elif ev_type in ("start_think", "end_think"):
            print(f"   [Чанк {i:02d}] Event: {ev_type}")

# Эмулируем finalize парсера
rem_events = parser.finalize()
for ev_type, ev_data in rem_events:
    if ev_type == "normal_chunk":
        stream_buffer += ev_data
        emitted_normal_text += ev_data

thought_data = parser.get_thought_data()
parser_normal_text = getattr(parser, "normal_text", "").strip() or emitted_normal_text.strip()

print("\n2. Состояние парсера после finalize():")
print(f"   - parser.normal_text:  {repr(parser_normal_text)}")
print(f"   - stream_buffer:       {repr(stream_buffer)}")
print(f"   - thought_data exists: {thought_data is not None}")

print("\n3. Анализ сценариев передачи текста в финализатор:")

# Сценарий А: Воркер вернул полный сырой текст генерации (с тегами)
raw_full_text = "".join(chunks)
cleaned_A = re.sub(r"<think>[\s\S]*?</think>", "", raw_full_text, flags=re.IGNORECASE).strip()
print(f"   [Сценарий А] Воркер вернул полный сырой стрим: len={len(raw_full_text)}")
print(f"                После re.sub: {repr(cleaned_A)}")

# Сценарий Б: Воркер вернул пустую строку
empty_text = ""
cleaned_B = re.sub(r"<think>[\s\S]*?</think>", "", empty_text, flags=re.IGNORECASE).strip()
print(f"   [Сценарий Б] Воркер вернул пустую строку: {repr(empty_text)}")
print(f"                После re.sub: {repr(cleaned_B)}")

# Проверяем логику подхвата
print("\n4. Проверка условий финализатора для Сценария Б:")
final_text = cleaned_B
if not final_text and parser_normal_text:
    final_text = parser_normal_text
    print(f"   -> Подхват из parser_normal_text сработал: {repr(final_text)}")
else:
    print(f"   -> Подхват НЕ сработал! final_text остался: {repr(final_text)}")

print("=" * 70)
