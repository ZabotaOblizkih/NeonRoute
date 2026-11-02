import sys
import json
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser
from services.session_manager import SessionManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder

print("=" * 80)
print("ПРОВЕРКА ОБНОВЛЕННОГО ПАЙПЛАЙНА ПОСЛЕ ПАТЧА")
print("=" * 80)

# 1. Модульный стресс-тест RoleplayPromptBuilder
print("\n[ТЕСТ 1] Проверка RoleplayPromptBuilder.build с принудительной подачей каста:")
card_path = ROOT / "characters" / "Кай и Эйла.json"
card = TavernParser.parse_file(str(card_path)) if card_path.exists() else None

dummy_cast = "Локация: slave market\nСпутники в отряде:\n• Kai\n• Eyla\nПрисутствующие персонажи (NPC):\n• Кай\n• Эйла"
dummy_lore = "\n<RecalledLore>\nВсплывшие воспоминания:\n• [Событие]: Дэниэл осмотрел цепи клетки 5 минут назад\n</RecalledLore>\n"

test_sys_prompt = RoleplayPromptBuilder.build(
    card,
    user_name="Дэниэл",
    active_cast_block=dummy_cast,
    recalled_lore_block=dummy_lore
)

cast_leaked = ("current active companions" in test_sys_prompt.lower()) or ("activecast" in test_sys_prompt.lower()) or ("slave market" in test_sys_prompt.lower())
lore_preserved = "recalledlore" in test_sys_prompt.lower() or "дэниэл осмотрел цепи" in test_sys_prompt.lower()

if not cast_leaked:
    print("  ✅ Активный каст успешно заблокирован в RoleplayPromptBuilder (0 утечек).")
else:
    print("  ❌ ВНИМАНИЕ: active_cast_block всё ещё просачивается в RoleplayPromptBuilder!")

if lore_preserved:
    print("  ✅ Векторная память (<RecalledLore>) успешно сохранена и передаётся в промпт.")
else:
    print("  ⚠️ Заметка: RecalledLore не найден в собранном промпте (проверь, передаётся ли аргумент).")

# 2. Сквозная сборка боевого пакета messages для сессии
print("\n[ТЕСТ 2] Сборка полного пакета сообщений для LM Studio:")
history = [
    {"role": "assistant", "content": "*Рынок рабов гудит грубыми шутками, звоном цепей и азартным торгом...*"},
    {"role": "user", "content": "Я подхожу к клетке и думаю, как бы их освободить"}
]

# Эмуляция вызова из RoleplayFlowController
messages = SessionManager.get_lm_studio_messages(
    history=history,
    is_roleplay=True,
    character_card=card,
    user_name="Дэниэл",
    history_limit=65,
    active_cast_block="",  # Согласно патчу передается пустая строка
    recalled_lore_block=dummy_lore
)

# Инъекция Depth-1 якоря
depth_anchor = RoleplayPromptBuilder.build_depth_anchor(
    card,
    user_name="Дэниэл",
    user_query=history[-1]["content"],
    custom_anchor=None
)

if depth_anchor and depth_anchor.strip():
    for idx in reversed(range(len(messages))):
        if messages[idx].get("role") == "user":
            anchor_msg = {"role": "system", "content": depth_anchor.strip()}
            messages.insert(idx, anchor_msg)
            break

print(f"Всего блоков в messages: {len(messages)}")
for idx, m in enumerate(messages):
    r = str(m.get("role")).upper()
    c = str(m.get("content", "")).replace("\n", " ").strip()
    preview = (c[:85] + "...") if len(c) > 85 else c
    print(f"  [{idx}] ROLE: {r:<9} | Длина: {len(m.get('content', '')):>5} симв. | {preview}")

# 3. Детальный аудит messages[0] (системного промпта диалога)
sys_content = messages[0]["content"]
print("\n" + "=" * 80)
print(f"АНАЛИЗ СИСТЕМНОГО ПРОМПТА MESSAGES[0] (Длина: {len(sys_content)} симв.)")
print("=" * 80)

sections = [line for line in sys_content.splitlines() if line.startswith("#")]
print("Обнаруженные заголовки секций:")
for s in sections:
    print(f"  • {s}")

markers_to_check = [
    "[Current Active Companions",
    "<ActiveCast>",
    "дочь работорговца",
    "slave collar"
]

found_poisons = [m for m in markers_to_check if m.lower() in sys_content.lower()]
if not found_poisons:
    print("\n✅ ЧИСТО: Ни одного маркера каста, инвентаря или галлюцинаций экстрактора в промпте нет.")
else:
    print(f"\n❌ ОБНАРУЖЕНЫ ОСТАТКИ: {found_poisons}")

dump_file = ROOT / "post_patch_payload_dump.txt"
with open(dump_file, "w", encoding="utf-8") as f:
    for idx, m in enumerate(messages):
        f.write(f"\n{'='*40} [MESSAGE {idx} | ROLE: {str(m.get('role')).upper()}] {'='*40}\n")
        f.write(str(m.get("content", "")))
        f.write("\n")

print(f"\nПолный дамп пакета сохранён в: {dump_file.relative_to(ROOT)}")
print("=" * 80)
