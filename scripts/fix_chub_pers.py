from pathlib import Path

p = Path("services/chub_client.py")
content = p.read_text(encoding="utf-8")

old_code = "clean_pers, pers_notes = sanitize_card_description(defn_tav_pers or defn_pers)"
new_code = """pers_parts = [p for p in (defn_pers, defn_tav_pers) if p]
                    raw_pers = "\\n\\n".join(dict.fromkeys(pers_parts))
                    clean_pers, pers_notes = sanitize_card_description(raw_pers)"""

if old_code in content:
    content = content.replace(old_code, new_code, 1)
    p.write_text(content, encoding="utf-8")
    print("[+] services/chub_client.py: Объединение defn_pers и defn_tav_pers восстановлено без потерь!")
else:
    print("[!] Искомая строка не найдена в services/chub_client.py")
