from pathlib import Path

print("=== 1. ПОИСК ОБРАБОТЧИКА КНОПКИ 'АРТ СЦЕНЫ' ===")
for p in Path("ui").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if any(k in txt for k in ("scene_art", "btn_scene_art", "_on_generate_scene_art", "_generate_scene_art")):
        for i, l in enumerate(txt.splitlines(), 1):
            if any(k in l for k in ("def _on_scene_art", "def _generate_scene_art", "btn_scene_art.clicked", "scene_art_requested")):
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")

print("\n=== 2. СБОРКА ПРОМПТА ДЛЯ АРТА СЦЕНЫ (СКИЛЛЫ, ЛОКАЦИЯ, ОРУЖИЕ) ===")
for p in Path("services").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "scene_art" in txt or "gallery" in txt.lower():
        for i, l in enumerate(txt.splitlines(), 1):
            if any(k in l for k in ("def build_scene_art_prompt", "def generate_scene_art", "gallery", "location", "inventory", "visual_dna")):
                if any(tag in l.lower() for tag in ("prompt", "art", "scene", "skill")):
                    print(f"{p.name}:{i:4d}: {l.strip()[:100]}")

print("\n=== 3. РЕАЛИЗАЦИЯ ГЕНЕРАЦИИ В services/art_director ИЛИ services/roleplay_flow_controller ===")
for target_name in ("art_director.py", "roleplay_flow_controller.py", "scene_art_service.py"):
    for p in Path("services").rglob(target_name):
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, l in enumerate(lines):
            if "scene_art" in l and "def " in l:
                print(f"\n--- {p.name}: {l.strip()} ---")
                for j in range(i, min(len(lines), i + 35)):
                    print(f"{j+1:4d}: {lines[j]}")
                break
