import re
from pathlib import Path

target_file = Path("services/tavern_models.py")
content = target_file.read_text(encoding="utf-8")

# Ищем блок чтения файла candidate_p и определяем его точный отступ
pattern = re.compile(r'([ \t]+)(with open\(candidate_p,\s*"r",\s*encoding="utf-8"\)\s*as\s*f:.*?\n[ \t]+disk_raw\s*=\s*json\.load\(f\)\s*\n)([ \t]*from services\.tavern_parser import TavernParser\s*\n)?([ \t]*disk_card\s*=\s*TavernParser\.parse_dict.*?\n)', re.DOTALL)

match = pattern.search(content)
if not match:
    # Запасной вариант: нормализация по контексту "with open(candidate_p"
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if "with open(candidate_p" in line:
            indent = line[:len(line) - len(line.lstrip())]
            # Заменяем строки после json.load
            new_lines = lines[:idx+2]
            new_lines.append(f"{indent}from services.tavern_parser import TavernParser")
            new_lines.append(f"{indent}disk_card = TavernParser.parse_dict(disk_raw, source_path=candidate_p)")
            
            # Пропускаем старые поломанные строки импорта и вызова parse_dict
            skip_idx = idx + 2
            while skip_idx < len(lines) and ("TavernParser" in lines[skip_idx] or not lines[skip_idx].strip()):
                skip_idx += 1
            new_lines.extend(lines[skip_idx:])
            content = "\n".join(new_lines)
            target_file.write_text(content, encoding="utf-8")
            print("[OK] Отступы успешно выровнены (fallback).")
            break
else:
    indent = match.group(1)
    correct_block = (
        f"{indent}with open(candidate_p, \"r\", encoding=\"utf-8\") as f:\n"
        f"{indent}    disk_raw = json.load(f)\n"
        f"{indent}from services.tavern_parser import TavernParser\n"
        f"{indent}disk_card = TavernParser.parse_dict(disk_raw, source_path=candidate_p)\n"
    )
    content = pattern.sub(correct_block, content, count=1)
    target_file.write_text(content, encoding="utf-8")
    print("[OK] Отступы импорта и вызова TavernParser успешно нормализованы!")
