import json
from pathlib import Path

# 1. Читаем последний cast_*.json
print("=== 1. СОДЕРЖИМОЕ ПОСЛЕДНЕГО CAST_*.JSON ===")
casts = sorted(Path("history").glob("cast_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
if casts:
    last_cast = casts[0]
    print(f"Файл: {last_cast.name} (размер: {last_cast.stat().st_size} байт)")
    try:
        with open(last_cast, "r", encoding="utf-8") as f:
            data = json.load(f)
        print("Ключи первого уровня:", list(data.keys()))
        for k in ("entities", "current_location", "locations", "story_milestones", "active_quest", "world_rules", "lore_entries", "scene_type"):
            if k in data:
                val = data[k]
                if isinstance(val, (dict, list)):
                    print(f"  {k}: count={len(val)} -> {str(val)[:160]}")
                else:
                    print(f"  {k}: {val}")
    except Exception as e:
        print(f"Ошибка чтения cast: {e}")
else:
    print("Файлы cast_*.json не найдены!")

# 2. Как CampaignInfoDialog загружает данные
print("\n=== 2. КАК CAMPAIGN_INFO_DIALOG ЗАГРУЖАЕТ ДАННЫЕ ===")
p_dlg = Path("ui/dialogs/campaign_info_dialog.py")
if p_dlg.exists():
    lines = p_dlg.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        if any(k in l for k in ("def __init__", "def _load", "def load", "RPCastManager", "cast_data", "lore_entries")):
            print(f"{p_dlg.name}:{i+1:4d}: {l.strip()[:100]}")
            for j in range(i+1, min(len(lines), i+15)):
                if lines[j].strip().startswith("def "):
                    break
                print(f"      {lines[j].strip()[:100]}")
            print("      ---")

# 3. Как открывается CampaignInfoDialog из MainWindow
print("\n=== 3. ВЫЗОВ ДИАЛОГА ИЗ MAIN_WINDOW / SIDEBAR ===")
for p in (Path("ui/main_window.py"), Path("ui/components/sidebar_widget.py")):
    if p.exists():
        txt = p.read_text(encoding="utf-8")
        for i, l in enumerate(txt.splitlines(), 1):
            if "CampaignInfoDialog" in l or "btn_campaign_info" in l:
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")
