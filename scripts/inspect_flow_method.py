from pathlib import Path
import re

ROOT = Path(r"C:\NeonRoute")
rfc_path = ROOT / "services" / "roleplay_flow_controller.py"

lines = rfc_path.read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("РОДИТЕЛЬСКИЙ МЕТОД И ПОДГОТОВКА MESSAGES В services/roleplay_flow_controller.py")
print("=" * 80)

# Ищем имя метода, которому принадлежат строки 200-350
current_method = None
method_start = 0
for idx, line in enumerate(lines[:350]):
    if re.match(r'^\s*def\s+([a-zA-Z0-9_]+)', line):
        current_method = line.strip()
        method_start = idx + 1

print(f"Активный метод: {current_method} (начиная со строки L{method_start})")
print("-" * 80)

# Выводим строки от начала метода (или L190) до L290
start_line = max(0, method_start - 1, 190)
end_line = min(len(lines), 310)

for i in range(start_line, end_line):
    prefix = "👉" if i in (261, 283) else "  "
    print(f"{prefix} L{i+1}: {lines[i]}")

print("=" * 80)
