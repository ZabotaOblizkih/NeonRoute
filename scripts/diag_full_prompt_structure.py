import sys
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))

from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

card = TavernParser.parse_file("characters/Isekai RPG.json")
sys_prompt = RoleplayPromptBuilder.build(card)

print("=" * 80)
print(f"1. СТРУКТУРА СИСТЕМНОГО ПРОМПТА (Всего {len(sys_prompt)} символов)")
print("=" * 80)

# Печатаем заголовки разделов в системном промпте
for line in sys_prompt.splitlines():
    if line.startswith("#") or line.startswith("<") and line.endswith(">"):
        print(f"  Раздел: {line}")

print("\n" + "=" * 80)
print("2. ПОЛНЫЙ ТЕКСТ СИСТЕМНОГО ПРОМПТА ПО БЛОКАМ")
print("=" * 80)
print(sys_prompt)

print("\n" + "=" * 80)
print("3. КАК СТРОИТСЯ ПАКЕТ СООБЩЕНИЙ (MESSAGES) ДЛЯ LLM В roleplay_flow_controller.py")
print("=" * 80)
p_rfc = Path("services/roleplay_flow_controller.py")
lines = p_rfc.read_text(encoding="utf-8").splitlines()
for i, l in enumerate(lines):
    if "RoleplayPromptBuilder.build" in l or "prepare_messages" in l or "build_context" in l:
        print(f"--- Найдена точка сборки на строке {i+1} ---")
        for j in range(max(0, i - 10), min(len(lines), i + 45)):
            print(f"{j+1:4d}: {lines[j]}")
        break
