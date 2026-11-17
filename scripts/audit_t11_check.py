from pathlib import Path

for rel, s, e in [
    ("services/engineer_parser.py", 115, 160),
    ("services/subagent_engineer.py", 715, 745),
]:
    print(f"\n=== {rel} L{s}..L{e} ===")
    lines = Path(rel).read_text(encoding="utf-8", errors="replace").splitlines()
    for i in range(s, min(len(lines), e) + 1):
        print(f"L{i:04d}: {lines[i-1]}")
