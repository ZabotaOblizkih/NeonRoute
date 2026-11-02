from pathlib import Path
cwb = Path(r"C:\NeonRoute\services\context_window_builder.py")
lines = cwb.read_text(encoding="utf-8").splitlines()
print("=" * 80)
print("services/context_window_builder.py: L915 - L935")
print("=" * 80)
for i in range(914, min(len(lines), 935)):
    print(f"L{i+1}: {lines[i]}")
print("=" * 80)
