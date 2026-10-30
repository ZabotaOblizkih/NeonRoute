from pathlib import Path

# 1. Где и как устроен apply_scene_updates_to_cast
print("=== 1. ЛОГИКА apply_scene_updates_to_cast ===")
for p in [Path("services/rp_cast_extractor.py"), Path("services/rp_cast_manager.py")]:
    if p.exists():
        txt = p.read_text(encoding="utf-8")
        if "def apply_scene_updates_to_cast" in txt:
            lines = txt.splitlines()
            for i, l in enumerate(lines):
                if "def apply_scene_updates_to_cast" in l:
                    print(f"Найден в {p.name} (строки {i+1}..{i+80}):")
                    for j in range(i, min(len(lines), i + 80)):
                        print(f"{j+1:4d}: {lines[j]}")
                    break

# 2. Как устроен _get_entity_keys в campaign_info_dialog.py
print("\n=== 2. CAMPAIGN_INFO_DIALOG: _get_entity_keys ===")
p_dlg = Path("ui/dialogs/campaign_info_dialog.py")
if p_dlg.exists():
    lines_dlg = p_dlg.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines_dlg):
        if "def _get_entity_keys" in l:
            for j in range(i, min(len(lines_dlg), i + 30)):
                print(f"{j+1:4d}: {lines_dlg[j]}")
            break

# 3. Список вкладок диалога и вкладка по умолчанию
print("\n=== 3. ИНИЦИАЛИЗАЦИЯ И НАЗВАНИЯ ВКЛАДОК ===")
for i, l in enumerate(lines_dlg):
    if any(k in l for k in ("addSubInterface", "addTab", "SegmentedWidget", "setCurrent")):
        print(f"{i+1:4d}: {l.strip()[:100]}")
