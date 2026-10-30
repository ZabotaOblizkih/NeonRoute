import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  ИССЛЕДОВАНИЕ: КАК ВЫВОДЯТСЯ СООБЩЕНИЯ И МЫСЛИ")
print("=" * 75)

# 1. Как chat_bubble_builder выбирает виджет для сообщений
print("\n[1] Логика выбора виджета в ui/chat_bubble_builder.py:")
cbb_path = Path("ui/chat_bubble_builder.py")
if cbb_path.exists():
    lines = cbb_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        if "def create_message_bubble" in l or "def _create" in l or "assistantchatmessagewidget" in l.lower():
            start = max(0, idx - 2)
            end = min(len(lines), idx + 25)
            print("\n".join(f"  {lines[i]}" for i in range(start, end)))
            break

# 2. Как engineer_controller отправляет сообщения и логи в UI
print("\n[2] Вызовы отправки сообщений в ui/controllers/engineer_controller.py:")
eng_path = Path("ui/controllers/engineer_controller.py")
if eng_path.exists():
    lines = eng_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        low = l.lower()
        if any(k in low for k in ["append_message", "add_message", "thought", "architect", "step", "status"]):
            if "def " in low or "self.append" in low or "self._append" in low or "infobar" in low:
                print(f"  Строка {idx+1}: {l.strip()[:100]}")

# 3. Как устроен рендеринг мыслей в ui/assistant_chat_bubble.py
print("\n[3] Поиск виджета/блока спойлера мыслей в ui/assistant_chat_bubble.py:")
acb_path = Path("ui/assistant_chat_bubble.py")
if acb_path.exists():
    lines = acb_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        low = l.lower()
        if any(k in low for k in ["think", "spoiler", "collapse", "reasoning", "disclosure", "accordion"]):
            if "def " in low or "class " in low or "=" in low:
                print(f"  Строка {idx+1}: {l.strip()[:100]}")

print("=" * 75)
