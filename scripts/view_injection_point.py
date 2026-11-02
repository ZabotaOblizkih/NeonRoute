from pathlib import Path
fc = Path(r"C:\NeonRoute\services\roleplay_flow_controller.py")
lines = fc.read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("services/roleplay_flow_controller.py: L265 - L300 (ТОЧКА ИНЪЕКЦИИ ЯКОРЯ)")
print("=" * 80)
for i in range(264, min(len(lines), 305)):
    print(f"L{i+1}: {lines[i]}")
print("=" * 80)
