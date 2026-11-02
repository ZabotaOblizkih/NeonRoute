from pathlib import Path
import re

ROOT = Path(r"C:\NeonRoute")

# 1. Проверяем добавление mes_example в prompt_builder
pb_path = ROOT / "services" / "roleplay_prompt_builder.py"
if pb_path.exists():
    lines = pb_path.read_text(encoding="utf-8").splitlines()
    print("=" * 80)
    print(" СБОРКА MES_EXAMPLE В ROLEPLAY_PROMPT_BUILDER")
    print("=" * 80)
    for i, line in enumerate(lines):
        if "mes_example" in line.lower() or "примеры" in line.lower():
            start = max(0, i - 2)
            end = min(len(lines), i + 6)
            print(f"--- Строка {i+1} ---")
            for j in range(start, end):
                print(f"  L{j+1}: {lines[j]}")

# 2. Где живут дефолты MODE_PROFILES и temp=1.0
print("\n" + "=" * 80)
print(" ДЕФОЛТЫ СЭМПЛИНГА (MODE_PROFILES / CONFIG)")
print("=" * 80)
for p in [ROOT / "config.py", ROOT / "services" / "session_manager.py"]:
    if p.exists():
        txt = p.read_text(encoding="utf-8")
        for match in re.finditer(r'(MODE_PROFILES\s*=\s*\{[\s\S]*?\n\})', txt):
            print(f"• Найдено в {p.name}:\n{match.group(1)}")
        for match in re.finditer(r'def get_default_sampling_params[\s\S]*?return\s+([^\n]+)', txt):
            print(f"• Функция в {p.name}:\n{match.group(0)}")
