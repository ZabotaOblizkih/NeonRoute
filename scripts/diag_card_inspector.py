import sys, json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config import CHARACTERS_DIR
from services.tavern_parser import TavernParser
from services.context_window_builder import ContextWindowBuilder

files = sorted(
    [f for f in CHARACTERS_DIR.glob("*.json") if not f.name.endswith(".tmp")],
    key=lambda x: x.stat().st_mtime,
    reverse=True
)

if not files:
    print("В папке characters нет JSON файлов!")
    sys.exit(1)

target_file = Path(sys.argv[1]) if len(sys.argv) > 1 else files[0]
print(f"=== АУДИТ КАРТОЧКИ: {target_file.name} ===")
card = TavernParser.parse_file(target_file)

fields = [
    "name", "description", "description_ru", "personality", "personality_ru",
    "scenario", "scenario_ru", "first_mes", "first_mes_ru", "mes_example", "mes_example_ru",
    "system_prompt", "post_history_instructions", "depth_prompt", "creator_notes",
    "visual_dna", "scenery_dna", "rpg_mechanics_block"
]

print("\n--- 1. РАСПАКОВАННЫЕ ПОЛЯ CHARACTER_CARD ---")
for f in fields:
    val = getattr(card, f, "") or ""
    val_str = str(val).replace("\n", " ")
    preview = (val_str[:85] + "...") if len(val_str) > 85 else val_str
    print(f"{f:27s}: len={len(str(val)):5d} | {preview}")

lore_entries = getattr(card, "lore_entries", []) or []
constants_cnt = sum(
    1 for e in lore_entries
    if getattr(e, "constant", False) or not getattr(e, "keys", [])
)
print(f"alternate_greetings        : count={len(card.alternate_greetings or [])}")
print(f"lore_entries               : count={len(lore_entries)} (constants={constants_cnt})")
print(f"companions                 : count={len(card.companions or [])}")
print(f"archetype_label            : {getattr(card, 'archetype_label', 'N/A')}")
print(f"is_minor                   : {getattr(card, 'is_minor', False)}")

greeting = card.first_mes_ru or card.first_mes or "*Действие начинается.*"
history = [
    {"role": "assistant", "content": greeting},
    {"role": "user", "content": "Осматриваюсь вокруг и внимательно слушаю."}
]

msgs = ContextWindowBuilder.get_lm_studio_messages(
    history=history,
    is_roleplay=True,
    character_card=card,
    user_name="Роман"
)

print("\n--- 2. ИТОГОВЫЙ МАССИВ MESSAGES ДЛЯ LLM (LM STUDIO) ---")
for i, m in enumerate(msgs):
    role = m.get("role")
    content = m.get("content", "")
    print(f"\n==================== [messages[{i}]] role={role} | chars={len(content)} ====================")
    print(content)
