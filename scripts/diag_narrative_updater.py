from pathlib import Path

p_upd = Path("services/rp_scene_updater.py")
if p_upd.exists():
    lines = p_upd.read_text(encoding="utf-8").splitlines()
    
    print("=== 1. ОБРАБОТКА ЛОКАЦИИ И ИНВЕНТАРЯ В RP_SCENE_UPDATER ===")
    for i, l in enumerate(lines):
        if any(k in l for k in ("new_location", "added_item", "removed_item", "location_dna")):
            for j in range(max(0, i-2), min(len(lines), i+15)):
                print(f"{j+1:4d}: {lines[j]}")
            print("    ---")
            break

    print("\n=== 2. ОБРАБОТКА ВЕХ ОТНОШЕНИЙ И БЛИЗОСТИ ===")
    for i, l in enumerate(lines):
        if any(k in l for k in ("intimacy_milestone", "relationship_context", "dynamic_tone", "relationship_shift")):
            for j in range(max(0, i-2), min(len(lines), i+20)):
                print(f"{j+1:4d}: {lines[j]}")
            print("    ---")
            break
