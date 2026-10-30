import sys
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=== 1. ТОЧНЫЙ КОД СБОРКИ MESSAGES В services/roleplay_flow_controller.py (строки 120-195) ===")
p_rfc = Path("services/roleplay_flow_controller.py")
lines = p_rfc.read_text(encoding="utf-8").splitlines()
for i in range(120, min(len(lines), 195)):
    print(f"{i+1:4d}: {lines[i]}")

print("\n" + "=" * 80)
print("=== 2. КАК ContextWindowBuilder СТРОИТ ИТОГОВЫЙ СПИСОК MESSAGES ===")
print("=" * 80)
p_cwb = Path("services/context_window_builder.py")
lines_cwb = p_cwb.read_text(encoding="utf-8").splitlines()
for i, l in enumerate(lines_cwb):
    if "def build_context" in l or "def build_messages" in l or "def get_messages" in l:
        print(f"--- Найден метод на строке {i+1}: {l.strip()} ---")
        for j in range(i, min(len(lines_cwb), i + 40)):
            print(f"{j+1:4d}: {lines_cwb[j]}")
        break

print("\n" + "=" * 80)
print("=== 3. СОДЕРЖИМОЕ FIRST_MES ИЗ КАРТОЧКИ (Isekai RPG.json) ===")
print("=" * 80)
from services.tavern_parser import TavernParser
card = TavernParser.parse_file("characters/Isekai RPG.json")
first_mes = getattr(card, "first_mes_ru", None) or getattr(card, "first_mes", "") or ""
print(f"Первые 400 символов first_mes:\n{first_mes[:400]}")
print(f"\nПоследние 400 символов first_mes:\n{first_mes[-400:]}")
