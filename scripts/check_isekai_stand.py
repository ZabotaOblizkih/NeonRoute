import json
from pathlib import Path
import urllib.request

print("=== 1. ПОИСК КАРТОЧЕК ПЕРСОНАЖЕЙ ===")
chars_dir = Path("characters")
found_cards = []
if chars_dir.exists():
    for f in chars_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            card_data = data.get("data", data)
            name = card_data.get("name", f.stem)
            desc = card_data.get("description", "")
            scenario = card_data.get("scenario", "")
            has_ru = bool(card_data.get("description_ru") or card_data.get("first_mes_ru"))
            is_isekai = any("isekai" in str(x).lower() for x in (name, desc, scenario, f.stem))
            found_cards.append({
                "path": str(f),
                "name": name,
                "is_isekai": is_isekai,
                "has_ru": has_ru,
                "desc_snippet": desc[:90].replace("\n", " ")
            })
        except Exception as e:
            pass

print(f"Всего найдено карточек: {len(found_cards)}")
isekai_matches = [c for c in found_cards if c["is_isekai"]]
print(f"Исекай совпадений: {len(isekai_matches)}")
for c in (isekai_matches or found_cards)[:5]:
    status_ru = "✅ Есть RU" if c["has_ru"] else "⏳ Только EN"
    print(f"  • {c['name']} [{status_ru}] -> {c['path']}")
    print(f"    {c['desc_snippet']}...")

print("\n=== 2. СТАТУС LLM-СЕРВЕРА ===")
server_urls = ["http://127.0.0.1:8080/health", "http://127.0.0.1:8080/v1/models"]
for u in server_urls:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "NeonRoute-Test"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            print(f"  [+] {u} отвечает (HTTP {resp.status})")
    except Exception as e:
        print(f"  [-] {u} недоступен: {e}")
