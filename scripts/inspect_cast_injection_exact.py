from pathlib import Path

ROOT = Path(r"C:\NeonRoute")

print("=" * 80)
print("1. services/roleplay_prompt_builder.py: 'Current Active Companions'")
print("=" * 80)
rpb_lines = (ROOT / "services" / "roleplay_prompt_builder.py").read_text(encoding="utf-8").splitlines()
for i, l in enumerate(rpb_lines):
    if "Current Active Companions" in l:
        for j in range(max(0, i - 15), min(len(rpb_lines), i + 25)):
            print(f"L{j+1}: {rpb_lines[j]}")
        break

print("\n" + "=" * 80)
print("2. services/rp_cast_manager.py: 'ActiveCast'")
print("=" * 80)
cast_lines = (ROOT / "services" / "rp_cast_manager.py").read_text(encoding="utf-8").splitlines()
for i, l in enumerate(cast_lines):
    if "ActiveCast" in l:
        for j in range(max(0, i - 10), min(len(cast_lines), i + 20)):
            print(f"L{j+1}: {cast_lines[j]}")
        break

print("\n" + "=" * 80)
print("3. services/roleplay_flow_controller.py: ПЕРЕДАЧА БЛОКОВ В СЕТЕВОЙ ПАКЕТ (L168-L215)")
print("=" * 80)
rfc_lines = (ROOT / "services" / "roleplay_flow_controller.py").read_text(encoding="utf-8").splitlines()
for i in range(167, min(len(rfc_lines), 215)):
    print(f"L{i+1}: {rfc_lines[i]}")
