from pathlib import Path

for rel, s, e in [
    ("services/engineer_parser.py", 285, 340),
    ("services/engineer_diagnostics.py", 178, 212),
]:
    print(f"\n=== {rel} L{s}..L{e} ===")
    lines = Path(rel).read_text(encoding="utf-8", errors="replace").splitlines()
    for i in range(s, min(len(lines), e) + 1):
        print(f"L{i:04d}: {lines[i-1]}")
