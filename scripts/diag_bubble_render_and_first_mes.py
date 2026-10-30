from pathlib import Path

# 1. Как RPChatMessageWidget инициализирует и отображает виджеты (строки 50..130)
p_rpc = Path("ui/components/rp/rp_chat_bubble.py")
if p_rpc.exists():
    lines = p_rpc.read_text(encoding="utf-8").splitlines()
    print("=== 1. RP_CHAT_BUBBLE.PY: WIDGET SETUP & TEXT HANDLING ===")
    for i in range(50, min(len(lines), 140)):
        print(f"{i+1:4d}: {lines[i]}")

# 2. Где именно first_mes вставляется в чат (поиск по MainWindow и контроллерам)
print("\n=== 2. ПОИСК ВСТАВКИ FIRST_MES В ОКНО ЧАТА ===")
for p in Path("ui").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "first_mes" in txt and any(k in txt for k in ("_append_message", "add_message", "RPChatMessageWidget")):
        for i, l in enumerate(txt.splitlines(), 1):
            if any(k in l for k in ("first_mes", "first_mes_ru", "_append_message")):
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")
