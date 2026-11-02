from pathlib import Path
ROOT = Path(r"C:\NeonRoute")

# 1. Что делает ContextWindowBuilder на строке 923
cwb_path = ROOT / "services" / "context_window_builder.py"
print("=" * 80)
print("services/context_window_builder.py: L905 - L950")
print("=" * 80)
if cwb_path.exists():
    lines = cwb_path.read_text(encoding="utf-8").splitlines()
    for i in range(max(0, 904), min(len(lines), 950)):
        print(f"L{i+1}: {lines[i]}")

# 2. Как RoleplayFlowController готовит messages до инъекции якоря
rfc_path = ROOT / "services" / "roleplay_flow_controller.py"
print("\n" + "=" * 80)
print("services/roleplay_flow_controller.py: L230 - L275")
print("=" * 80)
if rfc_path.exists():
    lines = rfc_path.read_text(encoding="utf-8").splitlines()
    for i in range(max(0, 229), min(len(lines), 275)):
        print(f"L{i+1}: {lines[i]}")
