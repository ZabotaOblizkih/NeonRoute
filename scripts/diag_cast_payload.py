import json
from pathlib import Path

# 1. Читаем из neonroute.log, какой именно JSON сгенерировала модель
print("=== 1. ЧТО СГЕНЕРИРОВАЛА МОДЕЛЬ (ИЗ ЛОГОВ NEONROUTE.LOG) ===")
p_log = Path("logs/neonroute.log")
if p_log.exists():
    lines = p_log.read_text(encoding="utf-8", errors="ignore").splitlines()
    found = [l for l in lines if any(k in l for k in ("[RPCast] Сырой JSON", "Кино-архетип", "apply_scene_updates"))]
    if found:
        print(f"Найдено записей: {len(found)}")
        for l in found[-10:]:
            print(f"  {l}")
    else:
        print("В neonroute.log не найдено строк со '[RPCast] Сырой JSON'")

# 2. Полное содержимое последнего cast_*.json
print("\n=== 2. ПОЛНОЕ СОДЕРЖИМОЕ ПОСЛЕДНЕГО CAST_*.JSON ===")
casts = sorted(Path("history").glob("cast_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
if casts:
    target = casts[0]
    print(f"Файл: {target.name}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"Ошибка чтения: {e}")

# 3. Как MainWindow открывает CampaignInfoDialog
print("\n=== 3. РЕАЛИЗАЦИЯ _open_campaign_info_dialog В UI/MAIN_WINDOW.PY ===")
p_mw = Path("ui/main_window.py")
if p_mw.exists():
    lines_mw = p_mw.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines_mw):
        if "def _open_campaign_info_dialog(" in l:
            for j in range(i, min(len(lines_mw), i + 35)):
                print(f"{j+1:4d}: {lines_mw[j]}")
            break

# 4. Как RPCastManager.apply_scene_updates обновляет каст
print("\n=== 4. ЛОГИКА RPCastManager.apply_scene_updates В SERVICES/RP_CAST_MANAGER.PY ===")
p_cm = Path("services/rp_cast_manager.py")
if p_cm.exists():
    lines_cm = p_cm.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines_cm):
        if "def apply_scene_updates(" in l:
            for j in range(i, min(len(lines_cm), i + 45)):
                print(f"{j+1:4d}: {lines_cm[j]}")
            break
