from pathlib import Path

# 1. Что вокруг строки 1452 в chat_flow_controller.py
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines = p_cf.read_text(encoding="utf-8").splitlines()
    start = max(0, 1435)
    end = min(len(lines), 1485)
    print(f"=== 1. CHAT_FLOW_CONTROLLER: СТРОКИ {start}..{end} ===")
    for i in range(start, end):
        print(f"{i+1:4d}: {lines[i]}")

# 2. Где в chat_flow_controller завершается стриминг / генерация
print("\n=== 2. ВСЕ МЕСТА С finished / on_llm_finished / stream_completed ===")
for i, line in enumerate(lines, 1):
    if any(k in line.lower() for k in ("handle_roleplay", "stream_finished", "generation_complete", "_on_llm_reply", "reply_finished")):
        print(f"{i:4d}: {line.strip()}")

# 3. Как устроен RPCastExtractor._run_extraction (вызов модели)
p_ext = Path("services/rp_cast_extractor.py")
if p_ext.exists():
    lines_ext = p_ext.read_text(encoding="utf-8").splitlines()
    print("\n=== 3. RP_CAST_EXTRACTOR._RUN_EXTRACTION (ВЫЗОВ МОДЕЛИ) ===")
    start_run = False
    for i, line in enumerate(lines_ext):
        if "def _run_extraction(" in line:
            start_run = True
        if start_run and i < 330:
            print(f"{i+1:4d}: {line}")
            if "chat_completion" in line:
                for j in range(i+1, min(len(lines_ext), i+20)):
                    print(f"{j+1:4d}: {lines_ext[j]}")
                break
