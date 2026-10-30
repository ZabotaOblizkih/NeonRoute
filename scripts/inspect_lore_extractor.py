from pathlib import Path

print("=== 1. ЧТО СОБИРАЛ RP_CAST_EXTRACTOR ===")
p_ext = Path("services/rp_cast_extractor.py")
if p_ext.exists():
    lines = p_ext.read_text(encoding="utf-8").splitlines()
    # Ищем системный промпт и спецификацию
    for i, l in enumerate(lines[:180]):
        if any(k in l for k in ("build_cast_extraction_spec", "SYSTEM_PROMPT", "class RPCastExtractor", "def extract_cast_async")):
            print(f"{i+1:4d}: {l}")
            for sub in lines[i+1:min(len(lines), i+25)]:
                print(f"      {sub}")
            print("      ...")

print("\n=== 2. ТОЧКА ВЫЗОВА В ROLEPLAY_FLOW_CONTROLLER ===")
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    text = p_rp.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if "_trigger_roleplay_memory_extraction" in l:
            start = max(0, i - 10)
            end = min(len(lines), i + 35)
            print(f"--- Строки {start+1} - {end} в {p_rp} ---")
            for idx in range(start, end):
                print(f"{idx+1:4d}: {lines[idx]}")
            break

print("\n=== 3. ТОЧКА ВЫЗОВА В CHAT_FLOW_CONTROLLER ===")
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines = p_cf.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        if "_trigger_memory_extraction" in l:
            start = max(0, i - 5)
            end = min(len(lines), i + 35)
            print(f"--- Строки {start+1} - {end} в {p_cf} ---")
            for idx in range(start, end):
                print(f"{idx+1:4d}: {lines[idx]}")
            break
