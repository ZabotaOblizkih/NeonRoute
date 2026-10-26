import sys
from pathlib import Path

root_dir = Path(".").resolve()
sys.path.insert(0, str(root_dir))

print("=" * 70)
print("ПОИСК И АНАЛИЗ ИНСТРУКЦИЙ <think> В СИСТЕМНОМ ПРОМПТЕ")
print("=" * 70)

# Ищем файлы билдера промптов ассистента
prompt_files = []
for p in (root_dir / "services").glob("*.py"):
    content = p.read_text(encoding="utf-8", errors="ignore")
    if "think" in content.lower() or "prompt" in p.name.lower():
        prompt_files.append((p, content))

for p, content in prompt_files:
    if "prompt" in p.name.lower() or "assistant" in p.name.lower():
        print(f"\n[ФАЙЛ]: {p.relative_to(root_dir)}")
        lines = content.splitlines()
        for i, l in enumerate(lines):
            if any(k in l.lower() for k in ["<think>", "thinking_protocol", "ход мыслей", "рассужд"]):
                start = max(0, i - 4)
                end = min(len(lines), i + 25)
                print(f"\n--- Фрагмент вокруг строки {i+1} ---")
                for idx in range(start, end):
                    print(f"{idx+1:04d}: {lines[idx]}")
                break

print("\n" + "=" * 70)
print("ТЕСТ СБОРКИ СИСТЕМНОГО ПРОМПТА АССИСТЕНТА")
print("=" * 70)
try:
    from services.assistant_prompt_builder import AssistantPromptBuilder
    builder = AssistantPromptBuilder()
    sys_prompt = builder.build_system_prompt()
    print(f"Длина системного промпта: {len(sys_prompt)} симв.")
    
    # Ищем блок с think в готовом промпте
    for line in sys_prompt.splitlines():
        if any(k in line.lower() for k in ["think", "рассужд", "протокол"]):
            print(f"  -> {line}")
except Exception as e:
    print(f"Ошибка вызова AssistantPromptBuilder: {e}")

print("=" * 70)
