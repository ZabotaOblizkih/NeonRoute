import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def snapshot_prod(root: Path) -> dict:
    state = {}
    for rel in ["config.json", "data/memory.db", "data/user_profiles.json", "knowledge_base/registry.json"]:
        p = root / rel
        state[rel] = p.stat().st_mtime_ns if p.exists() else None
    for folder in ["history", "workspace", "outputs", "data"]:
        d = root / folder
        if d.exists():
            state[folder] = {str(x.relative_to(root)): x.stat().st_mtime_ns for x in d.rglob("*") if x.is_file()}
    return state

before = snapshot_prod(PROJECT_ROOT)
print("[*] Запуск существующих тестов в tests/smoke/ для проверки их изоляции...")
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke", "-q", "--tb=short"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
print(res.stdout)
if res.stderr:
    print("STDERR:", res.stderr)

after = snapshot_prod(PROJECT_ROOT)

leaks = []
for k in before:
    if before[k] != after.get(k):
        if isinstance(before[k], dict):
            b_dict, a_dict = before[k], after.get(k, {})
            added = set(a_dict.keys()) - set(b_dict.keys())
            removed = set(b_dict.keys()) - set(a_dict.keys())
            modified = {f for f in (set(a_dict.keys()) & set(b_dict.keys())) if a_dict[f] != b_dict[f]}
            if added or removed or modified:
                leaks.append((k, {"added": list(added)[:5], "removed": list(removed)[:5], "modified": list(modified)[:5]}))
        else:
            leaks.append((k, f"{before[k]} -> {after.get(k)}"))

print("=== РЕЗУЛЬТАТ ПРОВЕРКИ ИЗОЛЯЦИИ tests/smoke/ ===")
if not leaks:
    print("[OK] Утечек в боевые папки не обнаружено.")
else:
    print("[!] ОБНАРУЖЕНО ЗАГРЯЗНЕНИЕ БОЕВОГО ПРИЛОЖЕНИЯ:")
    for item in leaks:
        print("  -", item)
