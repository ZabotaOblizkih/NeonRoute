from pathlib import Path

print("=" * 70)
print("  1. СОДЕРЖИМОЕ scripts/test_session_sync_stand.py")
print("=" * 70)
stand_path = Path("scripts/test_session_sync_stand.py")
if stand_path.exists():
    print(stand_path.read_text(encoding="utf-8"))
else:
    print("[!] scripts/test_session_sync_stand.py не найден")

print("\n" + "=" * 70)
print("  2. РЕАЛЬНЫЙ СРЕЗ services/tavern_models.py ВОКРУГ ПАТЧА")
print("=" * 70)
models_path = Path("services/tavern_models.py")
lines = models_path.read_text(encoding="utf-8").splitlines()

match_idx = None
for idx, line in enumerate(lines):
    if "loc_fields" in line:
        match_idx = idx
        break

if match_idx is not None:
    start = max(0, match_idx - 25)
    end = min(len(lines), match_idx + 35)
    for i in range(start, end):
        print(f"{i+1:4d}: {lines[i]}")
else:
    print("[!] Маркер 'loc_fields' не найден в файле")
