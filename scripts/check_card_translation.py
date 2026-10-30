import json
from pathlib import Path

path = Path(r"C:\NeonRoute\characters\Isekai RPG.json")
if not path.exists():
    print(f"Файл не найден: {path}")
    exit()

with open(path, "r", encoding="utf-8") as f:
    raw = json.load(f)

data = raw.get("data", raw) if isinstance(raw, dict) else {}

pairs = [
    ("description", "description_ru"),
    ("personality", "personality_ru"),
    ("scenario", "scenario_ru"),
    ("first_mes", "first_mes_ru"),
    ("mes_example", "mes_example_ru")
]

print(f"=== СТАТУС ПЕРЕВОДА НА ДИСКЕ: {path.name} ===\n")

for en_key, ru_key in pairs:
    en_val = str(data.get(en_key) or "").strip()
    ru_val = str(data.get(ru_key) or "").strip()
    
    en_len = len(en_val)
    ru_len = len(ru_val)
    
    status = "[OK] Переведено" if ru_len > 0 else "[--] Не переведено"
    en_preview = en_val.replace("\n", " ")[:80]
    print(f"{status} | {en_key} -> {ru_key}")
    print(f"  EN ({en_len} симв.): {en_preview}...")
    if ru_len > 0:
        ru_preview = ru_val.replace("\n", " ")[:80]
        print(f"  RU ({ru_len} симв.): {ru_preview}...")
    print()

alt_en = data.get("alternate_greetings") or []
alt_ru = data.get("alternate_greetings_ru") or []
print(f"alternate_greetings: EN = {len(alt_en)}, RU = {len(alt_ru)}")

sys_prompt = str(data.get("system_prompt") or "").strip()
sys_preview = sys_prompt.replace("\n", " ")[:80]
print(f"system_prompt (авторский): len={len(sys_prompt)} | {sys_preview}...")
