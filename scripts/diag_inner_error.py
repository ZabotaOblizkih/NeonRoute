from pathlib import Path
import sys

models_path = Path("services/tavern_models.py")
content = models_path.read_text(encoding="utf-8")

# 1. Проверяем импорт TavernParser в tavern_models.py
print("=== 1. ИМПОРТ TavernParser ===")
if "TavernParser" in content:
    for idx, line in enumerate(content.splitlines()):
        if "TavernParser" in line and ("import" in line or "class" in line):
            print(f"Строка {idx+1}: {line}")
else:
    print("[!] TavernParser вообще не упоминается в импортах!")

# 2. Смотрим начало from_dict (строки 450-515)
print("\n=== 2. НАЧАЛО from_dict ===")
lines = content.splitlines()
for i in range(450, 520):
    if i < len(lines):
        if "def from_dict" in lines[i] or "source_path" in lines[i]:
            print(f"Строка {i+1}: {lines[i]}")

# 3. Узнаем, падает ли try/except в реальном вызове:
# временно заменяем logger.debug в строке except на print(e)
print("\n=== 3. ТЕСТ РЕАЛЬНОГО ИСКЛЮЧЕНИЯ В try/except ===")
test_code = """
import sys
from pathlib import Path
ROOT_DIR = Path('.').resolve()
sys.path.insert(0, str(ROOT_DIR))
from services.tavern_models import CharacterCard

data = {
    'name': 'Isekai RPG',
    'source_path': str(Path('characters/Isekai RPG.json').resolve()),
    'description_ru': '',
    'personality_ru': ''
}
try:
    c = CharacterCard.from_dict(data)
    print('card.source_path в начале:', getattr(c, 'source_path', 'НЕТ'))
except Exception as err:
    print('Исключение при вызове:', err)
"""
exec(test_code)
