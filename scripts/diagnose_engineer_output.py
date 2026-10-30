import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  ИССЛЕДОВАНИЕ: ПАЙПЛАЙН ВЫДАЧИ СООБЩЕНИЙ В РЕЖИМЕ ИНЖЕНЕР")
print("=" * 75)

# 1. Ищем виджеты сообщений в ui/
print("\n[1] Виджеты сообщений и баблов в проекте:")
for p in Path("ui").rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "chatbubble" in txt.lower() or "messagebubble" in txt.lower() or "class message" in txt.lower():
            for idx, l in enumerate(txt.splitlines()):
                if l.strip().startswith("class ") and ("bubble" in l.lower() or "message" in l.lower()):
                    print(f"  • {p.name}:{idx+1} -> {l.strip()}")
    except Exception:
        pass

# 2. Как engineer_controller / task_lifecycle_controller добавляет сообщения в чат
print("\n[2] Точки добавления сообщений инженера в чат:")
targets = [
    Path("ui/controllers/engineer_controller.py"),
    Path("ui/controllers/task_lifecycle_controller.py"),
    Path("ui/controllers/chat_flow_controller.py")
]
for p in targets:
    if p.exists():
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        for idx, l in enumerate(lines):
            low = l.lower()
            if ("add_message" in low or "append_message" in low or "create_bubble" in low or "stream_chunk" in low or "architect" in low) and "def " in low:
                print(f"  • {p.name}:{idx+1} -> {l.strip()}")

# 3. Как сейчас парсятся мысли (<think>, рассуждения архитектора)
print("\n[3] Поиск обработки тегов мыслей (<think>, thought, reasoning):")
for p in Path("ui").rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "<think>" in txt or "think_content" in txt or "reasoning" in txt.lower():
            for idx, l in enumerate(txt.splitlines()):
                if "<think>" in l or "reasoning" in l.lower() and ("def " in l or "=" in l):
                    print(f"  • {p.name}:{idx+1} -> {l.strip()[:80]}")
    except Exception:
        pass

print("=" * 75)
