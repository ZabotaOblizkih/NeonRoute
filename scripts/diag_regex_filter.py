from pathlib import Path

# 1. Проверяем services/regex_filter.py на вырезание тегов <stats>
p_rf = Path("services/regex_filter.py")
if p_rf.exists():
    lines = p_rf.read_text(encoding="utf-8").splitlines()
    print("=== 1. SERVICES/REGEX_FILTER.PY (ПРАВИЛА ОЧИСТКИ ТЕКСТА) ===")
    for i, l in enumerate(lines):
        if any(k in l.lower() for k in ("<stats>", "stats", "clean", "sub", "process_output")):
            print(f"{i+1:4d}: {l.strip()[:100]}")

# 2. Проверяем, как в истории выглядят сообщения: были ли там <stats> или [STATUS
p_hist = Path("history")
chats = sorted(p_hist.glob("chat_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
if chats:
    txt = chats[0].read_text(encoding="utf-8")
    has_stats_tag = "<stats>" in txt or "[STATUS" in txt or "[СТАТУС" in txt
    print(f"\n=== 2. ПОСЛЕДНИЙ ЧАТ ({chats[0].name}) ===")
    print(f"Содержит теги <stats> или [STATUS]: {has_stats_tag}")
