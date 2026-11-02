from pathlib import Path
ROOT = Path(r"C:\NeonRoute")
pb_file = ROOT / "services" / "roleplay_prompt_builder.py"
lines = pb_file.read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("services/roleplay_prompt_builder.py: ПОИСК ДОБАВЛЕНИЯ MES_EXAMPLE В PROMPT_PARTS")
print("=" * 80)

# Ищем где в методе build упоминаются mes_example, examples или prompt_parts.append
for idx, line in enumerate(lines):
    if any(k in line.lower() for k in ["mes_example", "example_dialogue", "prompt_parts"]):
        start = max(0, idx - 1)
        end = min(len(lines), idx + 3)
        print(f"L{idx+1}: {line}")
