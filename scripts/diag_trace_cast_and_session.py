import sys
import json
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=== 1. ПОИСК СЕССИИ abf6f6fb В ДРУГИХ ПАПКАХ ===")
found = list(project_root.rglob("*abf6f6fb*"))
for f in found:
    print(f"  Файл/Папка: {f}")
    if f.is_file() and f.suffix == ".json":
        data = json.loads(f.read_text(encoding="utf-8"))
        print(f"    Ключи: {list(data.keys())}")
        if "history" in data:
            print(f"    Сообщений в истории: {len(data['history'])}")
            for idx, m in enumerate(data["history"]):
                print(f"      [{idx}] {m.get('role')}: {str(m.get('content'))[:120]}...")

print("\n=== 2. ПОЧЕМУ ДЛЯ СИСТЕМЫ ИНЖЕКТИТСЯ 'Спутница'? ===")
from services.rp_cast_manager import RPCastManager
p_cast = Path("services/rp_cast_manager.py")
if p_cast.exists():
    lines = p_cast.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines, 1):
        if "Спутница" in l or "focus_companion" in l or "get_cast_prompt_block" in l:
            print(f"  {i:4d}: {l.strip()[:100]}")
