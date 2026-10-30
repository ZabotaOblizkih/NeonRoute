from pathlib import Path

# 1. roleplay_flow_controller.py: строки 570..650 (генерация промпта)
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines = p_rp.read_text(encoding="utf-8").splitlines()
    print("=== 1. ROLEPLAY_FLOW_CONTROLLER: СТРОКИ 570..650 ===")
    for i in range(570, min(len(lines), 650)):
        print(f"{i+1:4d}: {lines[i]}")

# 2. hardware_engine.py: строки 440..500 (_on_scene_art_requested)
p_he = Path("ui/controllers/hardware_engine.py")
if p_he.exists():
    lines_he = p_he.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. HARDWARE_ENGINE: _on_scene_art_requested ===")
    for i in range(440, min(len(lines_he), 500)):
        print(f"{i+1:4d}: {lines_he[i]}")

# 3. assistant_prompt_builder.py: build_gallery_art_prompt
p_apb = Path("services/assistant_prompt_builder.py")
if p_apb.exists():
    lines_apb = p_apb.read_text(encoding="utf-8").splitlines()
    print("\n=== 3. ASSISTANT_PROMPT_BUILDER: build_gallery_art_prompt ===")
    for i, l in enumerate(lines_apb):
        if "def build_gallery_art_prompt" in l:
            for j in range(i, min(len(lines_apb), i+45)):
                print(f"{j+1:4d}: {lines_apb[j]}")
            break
