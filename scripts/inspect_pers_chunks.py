import json
from pathlib import Path

p = Path("characters/Isekai RPG.json")
with open(p, "r", encoding="utf-8") as f:
    raw = json.load(f)
data = raw.get("data", raw)
pers = data.get("personality", "")

print(f"Общий объем personality в оригинале: {len(pers)} симв.")

# Смотрим структуру абзацев
paras = [p.strip() for p in pers.split("\n\n") if p.strip()]
print(f"Всего смысловых блоков (по \\n\\n): {len(paras)}")

for idx, p in enumerate(paras, 1):
    preview = p[:80].replace("\n", " ")
    print(f"  [{idx}] ({len(p)} симв.): {preview}...")
