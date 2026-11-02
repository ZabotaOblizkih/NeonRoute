from pathlib import Path

ROOT = Path(r"C:\NeonRoute")

# 1. ContextWindowBuilder: combined_injection
cwb_path = ROOT / "services" / "context_window_builder.py"
print("=" * 80)
print("1. services/context_window_builder.py: СБОРКА combined_injection (L885 - L935)")
print("=" * 80)
lines = cwb_path.read_text(encoding="utf-8").splitlines()
for i in range(884, min(len(lines), 935)):
    print(f"L{i+1}: {lines[i]}")

# 2. RoleplayFlowController: кнопка Арт сцены и _directive
rfc_path = ROOT / "services" / "roleplay_flow_controller.py"
print("\n" + "=" * 80)
print("2. services/roleplay_flow_controller.py: АРТ-КНОПКА И АВТО-ДИРЕКТИВА")
print("=" * 80)
lines_rfc = rfc_path.read_text(encoding="utf-8").splitlines()
for i, line in enumerate(lines_rfc):
    if any(k in line.lower() for k in ["_generate_ephemeral_rp_art_prompt", "btn_art", "generate_art", "on_art_clicked", "_should_art"]):
        print(f"L{i+1}: {line.strip()[:100]}")

# 3. RoleplayPromptBuilder: дефолтные якоря и сохранение custom_anchor
rpb_path = ROOT / "services" / "roleplay_prompt_builder.py"
print("\n" + "=" * 80)
print("3. services/roleplay_prompt_builder.py: build_depth_anchor (L140 - L165)")
print("=" * 80)
lines_rpb = rpb_path.read_text(encoding="utf-8").splitlines()
for i in range(139, min(len(lines_rpb), 165)):
    print(f"L{i+1}: {lines_rpb[i]}")
