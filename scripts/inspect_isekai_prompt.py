import sys
import re
from pathlib import Path

root = Path(".").resolve()
target_file = root / "services" / "roleplay_prompt_builder.py"

print("=" * 80)
print(f" АНАЛИЗ ФАЙЛА: {target_file.name}")
print("=" * 80)

if not target_file.exists():
    print(f"❌ Файл не найден: {target_file}")
    sys.exit(1)

content = target_file.read_text(encoding="utf-8", errors="ignore")
print(f"Полный размер roleplay_prompt_builder.py: {len(content)} символов ({len(content.splitlines())} строк)")

# 1. Воспроизводим, что именно захватила регулярка из прошлого стенда
matches = re.findall(r'("""[\s\S]*?(?:stats|кубик|dnd|литрпг|isekai)[\s\S]*?""")', content, re.IGNORECASE)

if matches:
    captured = matches[0].strip('"\n ')
    print(f"\n[Захваченный стендом кусок]: {len(captured)} символов")
    print("-" * 80)
    print("НАЧАЛО ЗАХВАЧЕННОГО ТЕКСТА (первые 400 символов):")
    print(captured[:400])
    print("\n...\n")
    print("ХВОСТ ЗАХВАЧЕННОГО ТЕКСТА (последние 400 символов):")
    print(captured[-400:])
    print("-" * 80)
    
    # Сохраняем этот кусок в отдельный файл для наглядности
    dump_path = root / "debug_captured_blob.txt"
    dump_path.write_text(captured, encoding="utf-8")
    print(f"Полный захваченный текст сохранен в: {dump_path.name}")
else:
    print("Регулярка ничего не нашла.")

# 2. Ищем все строковые константы и шаблоны промптов в файле
print("\n" + "=" * 80)
print(" ВСЕ СТРОКОВЫЕ БЛОКИ (TRIPLE QUOTES) В ФАЙЛЕ:")
print("=" * 80)

# Ищем строгие блоки от """ до """
all_docstrings = re.findall(r'"""([\s\S]*?)"""', content)
for idx, doc in enumerate(all_docstrings, 1):
    cleaned = doc.strip()
    preview = cleaned.splitlines()[0] if cleaned.splitlines() else "(пусто)"
    print(f"[{idx}] Длина: {len(cleaned):5d} симв. | Превью: {preview[:70]}")

