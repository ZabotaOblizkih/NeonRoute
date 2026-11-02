from pathlib import Path
pb = Path(r"C:\NeonRoute\services\roleplay_prompt_builder.py")
lines = pb.read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("services/roleplay_prompt_builder.py: get_default_depth_anchor (L125 - L165)")
print("=" * 80)
for i in range(124, min(len(lines), 165)):
    print(f"L{i+1}: {lines[i]}")
print("=" * 80)
