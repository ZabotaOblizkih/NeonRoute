import sys
import json
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

card = TavernParser.parse_file(str(ROOT / "characters" / "Кай и Эйла.json"))

# 1. Где именно в карточке лежит этот текст?
fields_found = {}
for attr in ["description", "personality", "scenario", "creator_notes", "mes_example", "system_prompt"]:
    val = getattr(card, attr, "") or ""
    if "эмансипация" in val.lower() or "dark souls" in val.lower():
        fields_found[attr] = val[:300]

print("=" * 80)
print("1. В КАКИХ ПОЛЯХ КАРТОЧКИ НАХОДИТСЯ ЭТОТ ТЕКСТ:")
print("=" * 80)
for k, v in fields_found.items():
    print(f"• Поле '{k}':\n  {v}...\n")

# 2. Как этот блок выглядит в собранном системном промпте
sys_prompt = RoleplayPromptBuilder.build(card, user_name="Дэниэл")

print("=" * 80)
print("2. ОКРУЖЕНИЕ ЭТОГО ТЕКСТА В СОБРАННОМ СИСТЕМНОМ ПРОМПТЕ:")
print("=" * 80)
idx = sys_prompt.lower().find("эмансипация")
if idx != -1:
    start = max(0, idx - 200)
    end = min(len(sys_prompt), idx + 400)
    print(sys_prompt[start:end])
else:
    print("Текст не найден в RoleplayPromptBuilder.build(). Возможно, он подтягивается через RPCast/Lorebook.")
print("=" * 80)
