from pathlib import Path

target_file = Path("services/tavern_models.py")
content = target_file.read_text(encoding="utf-8")

old_line = "disk_card = TavernParser.parse_dict(disk_raw, source_path=candidate_p)"
new_lines = (
    "from services.tavern_parser import TavernParser\n"
    "                     disk_card = TavernParser.parse_dict(disk_raw, source_path=candidate_p)"
)

if "from services.tavern_parser import TavernParser" in content:
    print("[*] Локальный импорт уже присутствует в services/tavern_models.py.")
elif old_line in content:
    content = content.replace(old_line, new_lines, 1)
    target_file.write_text(content, encoding="utf-8")
    print("[OK] Локальный импорт TavernParser успешно внедрен в from_dict!")
else:
    print("[!] Не удалось найти точную строку disk_card = TavernParser.parse_dict(...)")
