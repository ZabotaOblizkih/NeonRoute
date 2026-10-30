from pathlib import Path

# 1. Где объявлен _open_campaign_info_dialog и что он передает
print("=== 1. РЕАЛИЗАЦИЯ _open_campaign_info_dialog ===")
for p in Path("ui").rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8")
        if "def _open_campaign_info_dialog(" in txt:
            print(f"Найден в {p}:")
            lines = txt.splitlines()
            for i, l in enumerate(lines):
                if "def _open_campaign_info_dialog(" in l:
                    for j in range(i, min(len(lines), i + 30)):
                        print(f"  {j+1:4d}: {lines[j]}")
                    break
    except Exception:
        pass

# 2. Как apply_scene_updates_to_cast сохраняет story_milestone и new_rule
p_cm = Path("services/rp_cast_manager.py")
if p_cm.exists():
    lines_cm = p_cm.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. КАК ОБРАБАТЫВАЕТСЯ story_milestone И new_rule В RP_CAST_MANAGER ===")
    for i, l in enumerate(lines_cm):
        if "def apply_scene_updates_to_cast(" in l:
            for j in range(i, min(len(lines_cm), i + 60)):
                if any(k in lines_cm[j] for k in ("story_milestone", "new_rule", "world_state", "active_quest", "npc")):
                    print(f"  {j+1:4d}: {lines_cm[j]}")

# 3. Как вкладка NPC (Второстепенные персонажи) отрисовывает сущности
p_dlg = Path("ui/dialogs/campaign_info_dialog.py")
if p_dlg.exists():
    lines_dlg = p_dlg.read_text(encoding="utf-8").splitlines()
    print("\n=== 3. КАК ОТОБРАЖАЮТСЯ ВТОРОСТЕПЕННЫЕ ПЕРСОНАЖИ (_refresh_cast_ui) ===")
    for i, l in enumerate(lines_dlg):
        if "def _refresh_cast_ui(" in l:
            for j in range(i, min(len(lines_dlg), i + 40)):
                print(f"  {j+1:4d}: {lines_dlg[j]}")
            break
