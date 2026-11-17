from pathlib import Path

lines = Path("services/engineer_tdd.py").read_text(encoding="utf-8", errors="replace").splitlines()
for label, start, end in [("IMPORTS L1..55", 1, 55), ("INCREMENTAL L450..678", 450, len(lines))]:
    print("\n" + "=" * 80)
    print(label)
    print("=" * 80)
    for i in range(max(1, start), min(len(lines), end) + 1):
        print(f"L{i:04d}: {lines[i-1]}")
