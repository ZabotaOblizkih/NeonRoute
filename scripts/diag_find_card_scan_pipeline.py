from pathlib import Path

# 1. Как устроен RPCastManager (load_cast, _default_cast, init_cast)
p_cm = Path("services/rp_cast_manager.py")
if p_cm.exists():
    lines = p_cm.read_text(encoding="utf-8").splitlines()
    print("=== 1. RP_CAST_MANAGER (СТРОКИ 35..110) ===")
    for i in range(35, min(len(lines), 115)):
        print(f"{i+1:4d}: {lines[i]}")

# 2. Где вызывается RPCastExtractor по всему проекту
print("\n=== 2. ВСЕ ВЫЗОВЫ RPCASTEXTRACTOR В ПРОЕКТЕ ===")
for p in Path(".").rglob("*.py"):
    if "venv" in str(p) or ".git" in str(p):
        continue
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "RPCastExtractor" in txt:
        for i, l in enumerate(txt.splitlines(), 1):
            if any(k in l for k in ("RPCastExtractor", "extract_cast", "extract_initial", "extract_scene", "analyze_scene")):
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")

# 3. Как roleplay_controller.py и main_window реагируют на выбор карточки
print("\n=== 3. ВЫБОР/СМЕНА КАРТОЧКИ В UI/CONTROLLERS ===")
for p in Path("ui/controllers").glob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if any(k in txt for k in ("set_active_character", "on_character_selected", "load_character", "init_session_cast")):
        for i, l in enumerate(txt.splitlines(), 1):
            if any(k in l for k in ("def ", "cast", "extractor", "scan")):
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")
