from pathlib import Path

print("=== 1. ТОЧНЫЕ ПУТИ К ФАЙЛАМ РАЗМЕТКИ ===")
for p in Path("ui").rglob("*.py"):
    if p.name in ("rpg_elements.py", "rp_chat_bubble.py", "chat_bubble.py"):
        print(f"Найден: {p}")

print("\n=== 2. ВСЕ ТЕГИ И МЕТОДЫ ПАРСИНГА В RPG_ELEMENTS ===")
for p in Path("ui").rglob("rpg_elements.py"):
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, l in enumerate(lines[:120], 1):
        if any(k in l for k in ("<stats>", "<thought>", "<choices>", "<meters>", "<check>", "<terminal>", "class ", "def parse", "def render")):
            print(f"{i:4d}: {l}")

print("\n=== 3. КАК RP_CHAT_BUBBLE ВЫЗЫВАЕТ RPG_ELEMENTS ===")
for p in Path("ui").rglob("rp_chat_bubble.py"):
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, l in enumerate(lines[:150], 1):
        if any(k in l for k in ("rpg_elements", "parse_", "render_", "<stats>", "style")):
            print(f"{i:4d}: {l}")
