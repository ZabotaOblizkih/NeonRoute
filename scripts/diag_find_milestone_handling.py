from pathlib import Path
import re

print("=== 1. ГДЕ ОПРЕДЕЛЕН apply_scene_updates / story_milestone ===")
for p in Path("services").glob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "story_milestone" in txt or "apply_scene_updates" in txt:
        for idx, line in enumerate(txt.splitlines(), 1):
            if any(k in line for k in ("def apply_scene_updates", "story_milestone", "new_rule", "world_state.append")):
                print(f"{p.name}:{idx:4d}: {line.strip()[:100]}")

print("\n=== 2. СТРУКТУРА ВКЛАДОК CAMPAIGN_INFO_DIALOG ===")
p_dlg = Path("ui/dialogs/campaign_info_dialog.py")
if p_dlg.exists():
    lines = p_dlg.read_text(encoding="utf-8").splitlines()
    for idx, l in enumerate(lines, 1):
        if any(k in l for k in ("addItem", "tab_state", "tab_inventory", "tab_scenario", "tab_cast", "tab_location", "SegmentedWidget")):
            if "self.segmented_bar.addItem" in l or "SegmentedWidgetItem" in l or "setCurrentItem" in l:
                print(f"{idx:4d}: {l.strip()}")
