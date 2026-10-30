import re
from pathlib import Path

target_file = Path("services/tavern_models.py")
content = target_file.read_text(encoding="utf-8")

# Ищем старый блок от needs_disk_sync до открытия файла
old_block_pattern = re.compile(
    r'([ \t]+)# Self-Healing: если source_path отсутствует или файл не существует на диске\s*\n'
    r'[ \t]+needs_disk_sync = not card\.source_path or not Path\(card\.source_path\)\.exists\(\)\s*\n'
    r'[ \t]+if needs_disk_sync and card\.name:\s*\n'
    r'([ \t]+)candidate_p = None.*?'
    r'([ \t]+)if candidate_p and candidate_p\.exists\(\):',
    re.DOTALL
)

match = old_block_pattern.search(content)
if not match:
    print("[!] Не удалось автоматически обнаружить блок needs_disk_sync по регулярному выражению.")
    print("Проверяем прямое вхождение...")
    if "needs_disk_sync" not in content:
        print("[*] needs_disk_sync уже отсутствует в файле.")
    exit(1)

base_indent = match.group(1)

new_logic = f"""{base_indent}# Self-Healing: синхронизация карточки с файлом на диске
{base_indent}candidate_p = None
{base_indent}if card.source_path and Path(card.source_path).exists():
{base_indent}    candidate_p = Path(card.source_path)
{base_indent}elif card.name:
{base_indent}    # Поиск по имени, если исходный путь потерян или не задан
{base_indent}    cand_exact = CHARACTERS_DIR / f"{{card.name}}.json"
{base_indent}    safe_name = "".join(c for c in card.name if c.isalnum() or c in (' ', '_', '-')).strip()
{base_indent}    cand_safe = CHARACTERS_DIR / f"{{safe_name}}.json" if safe_name else None

{base_indent}    if cand_exact.exists() and cand_exact.is_file():
{base_indent}        candidate_p = cand_exact
{base_indent}    elif cand_safe and cand_safe.exists() and cand_safe.is_file():
{base_indent}        candidate_p = cand_safe
{base_indent}    elif CHARACTERS_DIR.exists():
{base_indent}        c_name_lower = card.name.strip().lower()
{base_indent}        c_id = getattr(card, "id", None)
{base_indent}        for f_cand in CHARACTERS_DIR.glob("*.json"):
{base_indent}            if f_cand.suffix == ".tmp":
{base_indent}                continue
{base_indent}            try:
{base_indent}                with open(f_cand, "r", encoding="utf-8") as jf:
{base_indent}                    cd_raw = json.load(jf)
{base_indent}                target_meta = cd_raw.get("data", cd_raw) if ("spec" in cd_raw or "data" in cd_raw) else cd_raw
{base_indent}                disk_name = str(target_meta.get("name") or cd_raw.get("name") or "").strip().lower()
{base_indent}                disk_id = str(target_meta.get("id") or cd_raw.get("id") or "").strip()
{base_indent}                if c_id and disk_id and c_id != disk_id:
{base_indent}                    continue
{base_indent}                if (c_id and disk_id == c_id) or (disk_name and disk_name == c_name_lower and not disk_id):
{base_indent}                    candidate_p = f_cand
{base_indent}                    break
{base_indent}            except Exception:
{base_indent}                continue

{base_indent}if candidate_p and candidate_p.exists():"""

# Производим точную замену блока
new_content = old_block_pattern.sub(new_logic, content, count=1)
target_file.write_text(new_content, encoding="utf-8")
print("[OK] Логика Self-Healing в services/tavern_models.py успешно скорректирована!")
