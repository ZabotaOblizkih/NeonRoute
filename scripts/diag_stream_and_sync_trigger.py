from pathlib import Path

# 1. Проверяем строки 1315-1340 в chat_flow_controller.py (откуда вызывается _on_llm_response_received)
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines = p_cf.read_text(encoding="utf-8").splitlines()
    print("=== 1. CHAT_FLOW_CONTROLLER: СТРОКА 1326 И КОНТЕКСТ ВЫЗОВА ===")
    for i in range(max(0, 1310), min(len(lines), 1345)):
        print(f"{i+1:4d}: {lines[i]}")

    print("\n=== 2. CHAT_FLOW_CONTROLLER: КОНЕЦ _sync_turn (СТРОКИ 315-345) ===")
    for i in range(max(0, 315), min(len(lines), 346)):
        print(f"{i+1:4d}: {lines[i]}")

# 3. Проверяем сигналы воркера генерации в services/ (LMStudioClient или LlamaServer)
print("\n=== 3. СИГНАЛЫ В LM_CLIENT И ВОРКЕРАХ СТРИМИНГА ===")
for p in Path("services").rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8")
        if "response_received" in txt or "stream_finished" in txt:
            for i, l in enumerate(txt.splitlines(), 1):
                if any(k in l for k in ("pyqtSignal", "Signal", "response_received", "stream_finished", "finished.emit")):
                    print(f"{p.name}:{i:4d}: {l.strip()}")
    except Exception:
        pass
