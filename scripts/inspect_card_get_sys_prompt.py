from pathlib import Path
tm = Path(r"C:\NeonRoute\services\tavern_models.py")
lines = tm.read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("services/tavern_models.py: РЕАЛИЗАЦИЯ get_system_prompt")
print("=" * 80)
in_method = False
for idx, line in enumerate(lines):
    if "def get_system_prompt" in line:
        in_method = True
    if in_method:
        print(f"L{idx+1}: {line}")
        if line.strip().startswith("def ") and "get_system_prompt" not in line:
            break
        if idx > 0 and lines[idx-1].strip().startswith("return ") and not line.startswith("        "):
            break
