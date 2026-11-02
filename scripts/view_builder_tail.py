from pathlib import Path
pb = Path(r"C:\NeonRoute\services\roleplay_prompt_builder.py")
lines = pb.read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("services/roleplay_prompt_builder.py: L480 - L525 (ФИНАЛ СБОРКИ)")
print("=" * 80)
for i in range(479, min(len(lines), 525)):
    print(f"L{i+1}: {lines[i]}")

print("\n" + "=" * 80)
print("services/roleplay_prompt_builder.py: ПОИСК ФОРМИРОВАНИЯ rp_prompt")
print("=" * 80)
for i, line in enumerate(lines[:460]):
    if "rp_prompt" in line or "card.description" in line or "mes_example" in line:
        start = max(0, i - 1)
        end = min(len(lines), i + 3)
        for j in range(start, end):
            print(f"L{j+1}: {lines[j]}")
        print("-" * 40)
