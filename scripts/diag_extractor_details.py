from pathlib import Path
import re

print("=== 1. ЧТО ЗА МЕТОД СОДЕРЖИТ СТРОКУ 440 В ROLEPLAY_FLOW_CONTROLLER? ===")
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines = p_rp.read_text(encoding="utf-8").splitlines()
    # Выведем строки с 370 по 445
    for i in range(max(0, 369), min(len(lines), 445)):
        print(f"{i+1:4d}: {lines[i]}")

print("\n=== 2. ГДЕ ВЫЗЫВАЕТСЯ ЭТОТ МЕТОД ИЛИ _trigger_memory_extraction? ===")
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    text_cf = p_cf.read_text(encoding="utf-8")
    for i, line in enumerate(text_cf.splitlines(), 1):
        if "_trigger_memory_extraction" in line or "_trigger_roleplay" in line or "on_generation_finished" in line:
            print(f"chat_flow_controller.py:{i:4d}: {line.strip()}")

print("\n=== 3. ЧТО ВХОДИТ В SCAN_KEYWORDS В RP_CAST_EXTRACTOR? ===")
p_ext = Path("services/rp_cast_extractor.py")
if p_ext.exists():
    text_ext = p_ext.read_text(encoding="utf-8")
    lines_ext = text_ext.splitlines()
    for i, line in enumerate(lines_ext):
        if "SCAN_KEYWORDS" in line:
            for j in range(i, min(len(lines_ext), i + 25)):
                print(f"{j+1:4d}: {lines_ext[j]}")
                if "]" in lines_ext[j] or ")" in lines_ext[j]:
                    break
            break
