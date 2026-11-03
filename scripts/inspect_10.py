import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

lines = (PROJECT_ROOT / "services" / "memory_db.py").read_text(encoding="utf-8").splitlines()
print("=== memory_db.py lines 795..812 ===")
for i in range(max(0, len(lines)-25), len(lines)):
    print(f"{i+1}: {lines[i]}")

from services.tavern_parser import TavernParser
res = TavernParser.detect_card_archetype({"name": "System Guide", "description": "LitRPG [STATUS] Level Up"})
print("=== detect_card_archetype result ===")
print(res)
