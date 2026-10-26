import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

print("=" * 70)
print("1. ИСХОДНЫЙ КОД ui/components/thought_spoiler_widget.py")
print("=" * 70)
spoiler_path = root_dir / "ui" / "components" / "thought_spoiler_widget.py"
if spoiler_path.exists():
    for i, line in enumerate(spoiler_path.read_text(encoding="utf-8", errors="ignore").splitlines()):
        print(f"{i+1:03d}: {line}")
else:
    print("Файл thought_spoiler_widget.py не найден!")

print("\n" + "=" * 70)
print("2. ФИНАЛИЗАЦИЯ И ПЕРЕДАЧА МЫСЛЕЙ В assistant_flow_controller.py (строки 840-880)")
print("=" * 70)
af_path = root_dir / "ui" / "controllers" / "assistant_flow_controller.py"
if af_path.exists():
    lines = af_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx in range(839, min(len(lines), 880)):
        print(f"{idx+1:04d}: {lines[idx]}")

print("=" * 70)
