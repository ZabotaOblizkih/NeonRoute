import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

print("=" * 75)
print("  ИССЛЕДОВАНИЕ: ОБЪЯВЛЕНИЕ _append_message И ПЕРЕКЛЮЧЕНИЕ СЕССИЙ")
print("=" * 75)

# 1. Поиск точного объявления _append_message по всей кодовой базе ui/
print("\n[1] Поиск определения '_append_message' в ui/:")
for p in Path("ui").rglob("*.py"):
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
        if "def _append_message" in content:
            lines = content.splitlines()
            for idx, l in enumerate(lines):
                if "def _append_message" in l:
                    rel_p = p.resolve().relative_to(ROOT_DIR)
                    print(f"  • {rel_p}:{idx+1} -> {l.strip()}")
                    # Печатаем первые 15 строк реализации
                    print("\n".join(f"      {lines[i]}" for i in range(idx + 1, min(idx + 15, len(lines)))))
    except Exception as e:
        print(f"Ошибка чтения {p}: {e}")

# 2. Поиск метода переключения сессий (клик по истории чатов)
print("\n[2] Поиск метода загрузки/переключения сессии в ui/controllers/ и ui/main_window.py:")
for p in list(Path("ui/controllers").glob("*.py")) + [Path("ui/main_window.py")]:
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        for idx, l in enumerate(lines):
            low = l.lower()
            if any(k in low for k in ["def load_session", "def _load_session", "def switch_session", "def _switch_session", "def select_session"]):
                rel_p = p.resolve().relative_to(ROOT_DIR)
                print(f"  • {rel_p}:{idx+1} -> {l.strip()}")
    except Exception:
        pass

# 3. Как очищается чат при смене сессии (clear_chat, clear_layout)
print("\n[3] Очистка сообщений при смене сессии:")
for p in [Path("ui/main_window.py"), Path("ui/controllers/chat_flow_controller.py")]:
    if p.exists():
        content = p.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        for idx, l in enumerate(lines):
            if any(k in l for k in ["def clear_chat", "def _clear_chat", "def _clear_messages", "def clear_messages"]):
                rel_p = p.resolve().relative_to(ROOT_DIR)
                print(f"  • {rel_p}:{idx+1} -> {l.strip()}")

print("=" * 75)
