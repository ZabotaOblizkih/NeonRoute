from pathlib import Path
pb = Path(r"C:\NeonRoute\services\roleplay_prompt_builder.py")
lines = pb.read_text(encoding="utf-8").splitlines()
print("=" * 80)
print("services/roleplay_prompt_builder.py: L445 - L485")
print("=" * 80)
for i in range(444, min(len(lines), 485)):
    print(f"L{i+1}: {lines[i]}")
print("=" * 80)
