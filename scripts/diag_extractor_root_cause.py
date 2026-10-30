from pathlib import Path
import re

print("=== 1. ЗАГОЛОВОК МЕТОДА СО СТРОКОЙ 440 В ROLEPLAY_FLOW_CONTROLLER ===")
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines = p_rp.read_text(encoding="utf-8").splitlines()
    # Ищем def перед строкой 370
    for i in range(369, 250, -1):
        if lines[i].strip().startswith("def "):
            print(f"Родительский метод найден на строке {i+1}:")
            for j in range(i, min(len(lines), i + 25)):
                print(f"{j+1:4d}: {lines[j]}")
            break

print("\n=== 2. ВСЕ ВЫЗОВЫ _trigger_memory_extraction И _trigger_roleplay_memory_extraction ===")
for p in Path(".").rglob("*.py"):
    if any(ign in p.parts for ign in [".git", "__pycache__", "venv", ".venv"]):
        continue
    try:
        txt = p.read_text(encoding="utf-8")
        for idx, line in enumerate(txt.splitlines(), 1):
            if ("_trigger_memory_extraction" in line or "_trigger_roleplay_memory_extraction" in line) and not line.strip().startswith("def "):
                print(f"{p}:{idx:4d}: {line.strip()}")
    except Exception:
        pass

print("\n=== 3. СОДЕРЖИМОЕ SCAN_KEYWORDS В SERVICES/RP_CONSTANTS.PY ===")
p_const = Path("services/rp_constants.py")
if p_const.exists():
    txt_const = p_const.read_text(encoding="utf-8")
    for i, line in enumerate(txt_const.splitlines(), 1):
        if "SCAN_KEYWORDS" in line:
            for j in range(i - 1, min(len(txt_const.splitlines()), i + 30)):
                l = txt_const.splitlines()[j]
                print(f"{j+1:4d}: {l}")
                if "]" in l or ")" in l:
                    break
            break
