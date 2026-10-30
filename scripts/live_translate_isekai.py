import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent if "__file__" in locals() else Path.cwd()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtWidgets import QApplication
from services.tavern_parser import TavernParser
from services.lm_studio import LMStudioClient
from services.card_translator import translate_card_slots_sequential
from ui.components.step_progress_button import StepProgressButton, CardProgressBridge

app = QApplication.instance() or QApplication(sys.argv)

card_path = Path("characters/Isekai RPG.json")
if not card_path.exists():
    print(f"[-] Карточка {card_path} не найдена!")
    sys.exit(1)

print(f"=== 1. ЗАГРУЗКА КАРТОЧКИ: {card_path.name} ===")
card = TavernParser.parse_file(card_path)
print(f"  Имя: {card.name}")
print(f"  Описание (EN, {len(card.description or '')} симв.): {(card.description or '')[:120]}...")
print(f"  Сценарий (EN, {len(card.scenario or '')} симв.): {(card.scenario or '')[:120]}...")
print(f"  Первое сообщение (EN, {len(card.first_mes or '')} симв.): {(card.first_mes or '')[:120]}...")

# Подключаем реальный LM-клиент к работающему llama-server на порту 1234
lm_client = LMStudioClient(base_url="http://127.0.0.1:1234/v1")

# Инициализируем виджет кнопки и потокобезопасный мост
btn = StepProgressButton()
bridge = CardProgressBridge()
bridge.progress_changed.connect(btn.update_step_progress)

def on_step(cur: int, tot: int, slot: str):
    bridge.progress_changed.emit(cur, tot, slot)
    app.processEvents()
    print(f"  [⚡ Слот {cur}/{tot}] {slot:<16} -> Кнопка: '{btn.text()}' | Кольцо/Бар: {btn.ring.value()}%")

print("\n=== 2. СТАРТ ЖИВОГО ПОШАГОВОГО ПЕРЕВОДА (LLAMA-SERVER:1234) ===")
try:
    res_dict = translate_card_slots_sequential(
        card=card,
        lm_client=lm_client,
        on_progress=on_step
    )
    btn.reset_state(success=True)
    app.processEvents()
    print(f"\n[+] Перевод успешно завершён! Всего слотов: {len(res_dict)}")

    print("\n=== 3. РЕЗУЛЬТАТЫ ПЕРЕВОДА НА РУССКИЙ ЯЗЫК ===")
    for k, v in res_dict.items():
        print(f"\n--- [{k}] ---")
        print(v.strip())

except Exception as e:
    btn.reset_state(success=False)
    app.processEvents()
    print(f"[-] Ошибка во время выполнения перевода: {e}")
    import traceback
    traceback.print_exc()

app.quit()
