import json
from pathlib import Path

# Читаем последний файл каста
casts = sorted(Path("history").glob("cast_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
if not casts:
    print("Каст не найден.")
    exit()

last_cast = casts[0]
with open(last_cast, "r", encoding="utf-8") as f:
    c = json.load(f)

print(f"=== АНАЛИЗ ОТОБРАЖЕНИЯ КАСТА В ОКНЕ ({last_cast.name}) ===\n")

print("📁 ВКЛАДКА 1: 'Память, сюжет и статус' (tab_state) — открывается первой:")
print(f"  • Квест (active_quest): '{c.get('active_quest') or '[СВОБОДНО]'}'")
print(f"  • Правила мира (world_state): {c.get('world_state') or 'ПУСТО'}")
print(f"  • Вехи отношений (intimacy_milestones): {c.get('intimacy_milestones') or 'ПУСТО'}")

print("\n📁 ВКЛАДКА 2: 'Инвентарь и снаряжение' (tab_inventory):")
print(f"  • Предметы: {c.get('inventory') or 'ПУСТО'}")

print("\n📁 ВКЛАДКА 3: 'Сценарий и завязка' (tab_scenario):")
print("  • Статический лор карточки и описание персонажа.")

print("\n📁 ВКЛАДКА 4: 'Второстепенные персонажи' (tab_cast):")
entities = c.get("entities", {})
print(f"  • Всего сущностей в касте: {len(entities)}")
for eid, ent in entities.items():
    role = ent.get("role", "npc")
    name = ent.get("canonical_name", eid)
    facts = ent.get("facts", [])
    presence = ent.get("presence", "physical")
    print(f"    -> [{role.upper()}] {name} (присутствие: {presence}):")
    for f in facts:
        print(f"       * {f}")

print("\n📁 ВКЛАДКА 5: 'Локация и атмосфера' (tab_location):")
print(f"  • Локация: '{c.get('current_location') or 'Не указана'}'")
print(f"  • Визуальный кадр: '{c.get('last_visual_prompt') or 'ПУСТО'}'")
