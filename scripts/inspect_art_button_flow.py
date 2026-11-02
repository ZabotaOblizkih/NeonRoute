from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
rfc_path = ROOT / "services" / "roleplay_flow_controller.py"
lines = rfc_path.read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("services/roleplay_flow_controller.py: МЕТОД ГЕНЕРАЦИИ АРТА ПО КНОПКЕ (L614 - L670)")
print("=" * 80)
for i in range(613, min(len(lines), 670)):
    print(f"L{i+1}: {lines[i]}")
print("=" * 80)
