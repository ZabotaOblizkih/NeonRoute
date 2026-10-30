from pathlib import Path

print("=== 1. ПОИСК ФУНКЦИЙ ИНИЦИАЛИЗАЦИИ КАСТА И АНАЛИЗА КАРТОЧЕК В SERVICES ===")
for p in Path("services").glob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if any(k in txt.lower() for k in ("init_cast", "setup_cast", "scan_card", "initial_scene", "analyze_scene", "first_mes")):
        for i, l in enumerate(txt.splitlines(), 1):
            if any(k in l.lower() for k in ("def init", "def setup", "def scan", "def parse", "def extract")) and any(c in l.lower() for c in ("cast", "card", "scene", "session")):
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")

print("\n=== 2. КАК УСТРОЕН RP_CAST_MANAGER: load_cast / default_cast ===")
p_cm = Path("services/rp_cast_manager.py")
if p_cm.exists():
    lines = p_cm.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines[:80]):
        if any(k in l for k in ("def load_cast", "def _default_cast", "def save_cast", "class RPCastManager")):
            print(f"{i+1:4d}: {l}")
            for j in range(i+1, min(len(lines), i+25)):
                if lines[j].strip().startswith("def "):
                    break
                print(f"      {lines[j].strip()[:90]}")
            print("      ---")

print("\n=== 3. КАК ВЫЗЫВАЕТСЯ АНАЛИЗ СЦЕНЫ В ROLEPLAY_FLOW_CONTROLLER / CONTROLLER ===")
for p in Path("services").glob("*controller*.py"):
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, l in enumerate(lines):
        if any(k in l for k in ("_update_scene", "scene_updater", "update_cast", "on_turn_completed", "process_turn")):
            if "def " in l:
                print(f"{p.name}:{i+1:4d}: {l.strip()[:100]}")
