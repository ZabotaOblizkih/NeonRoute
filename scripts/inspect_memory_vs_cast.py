from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
cast_lines = (ROOT / "services" / "rp_cast_manager.py").read_text(encoding="utf-8").splitlines()

print("=" * 80)
print("services/rp_cast_manager.py: СБОРКА ВОСПОМИНАНИЙ И ЛОРА (L715 - L755)")
print("=" * 80)
for i in range(714, min(len(cast_lines), 755)):
    print(f"L{i+1}: {cast_lines[i]}")
print("=" * 80)
