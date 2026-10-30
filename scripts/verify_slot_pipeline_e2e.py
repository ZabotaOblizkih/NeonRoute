import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtWidgets import QApplication
from services.tavern_parser import CharacterCard
from services.card_translator import translate_card_slots_sequential
from ui.components.step_progress_button import StepProgressButton, CardProgressBridge

app = QApplication.instance() or QApplication(sys.argv)

# 1. Тестовая карточка со всеми слотами
card = CharacterCard(
    name="Элария",
    description="A skilled navigator in the neon-lit district of Kaelen.",
    personality="Calm, observant, fiercely loyal to her crew.",
    scenario="A dark alley in Neon City during heavy rain.",
    mes_example="<START>\n{{user}}: Are you ready?\n{{char}}: Always.",
    first_mes="The neon signs flicker through the rain. 'We move now,' Elaria whispers."
)
card.alternate_greetings = [
    "Rain drips from her cybernetic arm as she turns to you.",
    "Inside the back alley workshop, Elaria cleans her optics."
]

# 2. Мок LLM-клиента
class MockLmClient:
    def __init__(self):
        self.call_count = 0

    def chat_completion(self, messages, **kwargs):
        self.call_count += 1
        user_content = messages[-1]["content"] if messages else ""
        return f"[RU Перевод #{self.call_count}] {user_content[:40]}..."

mock_lm = MockLmClient()

# 3. Виджет кнопки и мост сигналов
btn = StepProgressButton()
bridge = CardProgressBridge()
bridge.progress_changed.connect(btn.update_step_progress)

progress_history = []
def _on_progress(cur, tot, slot):
    bridge.progress_changed.emit(cur, tot, slot)
    app.processEvents()
    progress_history.append((cur, tot, slot, btn.text(), btn.ring.value(), btn.bar.value()))

print("=== 1. ЗАПУСК ПОШАГОВОГО КОНВЕЙЕРА (СЛОТ-BY-СЛОТ) ===")
res_dict = translate_card_slots_sequential(
    card=card,
    lm_client=mock_lm,
    on_progress=_on_progress
)

print(f"[+] Всего слотов переведено: {len(res_dict)}")
for k, v in res_dict.items():
    print(f"  - {k}: {v[:60]}...")

print("\n=== 2. ИСТОРИЯ СОСТОЯНИЙ КНОПКИ StepProgressButton ===")
for cur, tot, slot, txt, ring_val, bar_val in progress_history:
    print(f"  Шаг {cur}/{tot} [{slot}]: text='{txt}', ring={ring_val}%, bar={bar_val}%")

btn.reset_state(success=True)
app.processEvents()
print(f"\n[+] Сброс кнопки: text='{btn.text()}', ring_visible={btn.ring.isVisible()}, bar_visible={btn.bar.isVisible()}")
print("[+] Все проверки пройдены успешно!")
app.quit()
