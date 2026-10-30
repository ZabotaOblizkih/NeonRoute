import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.engineer_prompts import get_architect_prompt

print("=== Проверка текущего промпта Архитектора на диске ===")
txt = get_architect_prompt(ws_files=["game.py"])
if '"review"' in txt:
    print("✅ Правило intent 'review' ПРИСУТСТВУЕТ в services/engineer_prompts.py")
else:
    print("❌ Правило intent 'review' ОТСУТСТВУЕТ в services/engineer_prompts.py")

for line in txt.splitlines():
    if "review" in line.lower():
        print(f"   -> {line}")
