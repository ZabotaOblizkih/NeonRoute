import shutil
import re
from pathlib import Path

# --- 1. ПАТЧ services/tavern_models.py ---
models_path = Path("services/tavern_models.py")
content = models_path.read_text(encoding="utf-8")

if "description_ru" in content and "loc_fields" in content:
    print("[*] services/tavern_models.py уже содержит патч. Пропуск модификации.")
else:
    # Ищем блок companions для привязки по отступам
    pattern = re.compile(r'(\s+)(if not card\.companions and getattr\(disk_card, "companions", None\):.*?\n\1\s+card\.is_group = True)', re.DOTALL)
    match = pattern.search(content)

    if not match:
        print("[!] Не удалось автоматически обнаружить блок companions в from_dict.")
        exit(1)

    indent = match.group(1)
    matched_block = match.group(0)

    patch_snippet = (
        f"{matched_block}\n\n"
        f"{indent}# ---------------------------------------------------------\n"
        f"{indent}# Self-Healing: синхронизация локализации (_ru) с диска\n"
        f"{indent}# ---------------------------------------------------------\n"
        f"{indent}loc_fields = [\n"
        f"{indent}    'description_ru',\n"
        f"{indent}    'personality_ru',\n"
        f"{indent}    'scenario_ru',\n"
        f"{indent}    'first_mes_ru',\n"
        f"{indent}    'mes_example_ru',\n"
        f"{indent}]\n"
        f"{indent}for field in loc_fields:\n"
        f"{indent}    s_val = getattr(card, field, None)\n"
        f"{indent}    d_val = getattr(disk_card, field, None)\n"
        f"{indent}    if (s_val is None or (isinstance(s_val, str) and not s_val.strip())) and d_val:\n"
        f"{indent}        setattr(card, field, d_val)\n\n"
        f"{indent}s_alts = getattr(card, 'alternate_greetings_ru', None) or []\n"
        f"{indent}d_alts = getattr(disk_card, 'alternate_greetings_ru', None) or []\n"
        f"{indent}if not any(bool(alt and alt.strip()) for alt in s_alts) and d_alts:\n"
        f"{indent}    card.alternate_greetings_ru = list(d_alts)\n"
        f"{indent}elif s_alts and d_alts and len(s_alts) < len(d_alts):\n"
        f"{indent}    m_alts = list(s_alts)\n"
        f"{indent}    for i in range(len(s_alts), len(d_alts)):\n"
        f"{indent}        m_alts.append(d_alts[i])\n"
        f"{indent}    card.alternate_greetings_ru = m_alts"
    )

    new_content = content.replace(matched_block, patch_snippet, 1)
    models_path.write_text(new_content, encoding="utf-8")
    print("[OK] services/tavern_models.py успешно пропатчен.")

# --- 2. ОБНОВЛЕНИЕ БОЕВОЙ КАРТОЧКИ Isekai RPG.json ---
src_card = Path("characters/Isekai RPG.test_translated.json")
dst_card = Path("characters/Isekai RPG.json")
bak_card = Path("characters/Isekai RPG.json.bak")

if src_card.exists():
    if not bak_card.exists() and dst_card.exists():
        shutil.copy2(dst_card, bak_card)
        print(f"[+] Создан бэкап: {bak_card.name}")
    shutil.copy2(src_card, dst_card)
    print(f"[OK] {dst_card.name} обновлен проверенным переводом.")
else:
    print(f"[!] Файл {src_card.name} не найден, обновление карточки пропущено.")
