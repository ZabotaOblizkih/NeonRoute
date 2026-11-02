from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
rfc_path = ROOT / "services" / "roleplay_flow_controller.py"
lines = rfc_path.read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("services/roleplay_flow_controller.py: L125 - L195 (_enqueue_roleplay_llm_task)")
print("=" * 80)
for i in range(124, 195):
    print(f"L{i+1}: {lines[i]}")
print("=" * 80)
