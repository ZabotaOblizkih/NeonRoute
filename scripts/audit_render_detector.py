from pathlib import Path

print("=== code_validator.py L315..370 ===")
lines = Path("services/code_validator.py").read_text(encoding="utf-8", errors="replace").splitlines()
for i in range(315, min(len(lines), 370) + 1):
    print(f"L{i:04d}: {lines[i-1]}")

print("\n=== Свежие проекты и .neonroute_last_failure.json ===")
proj_root = Path("workspace") / "projects"
if proj_root.exists():
    for d in sorted([x for x in proj_root.iterdir() if x.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)[:2]:
        print(f"Dir: {d.name}")
        for f in d.iterdir():
            if f.name != ".git":
                print(f"  - {f.name} ({f.stat().st_size if f.is_file() else 'DIR'})")
        lf = d / ".neonroute_last_failure.json"
        if lf.exists():
            print("  last_failure:", lf.read_text(encoding="utf-8", errors="replace"))
