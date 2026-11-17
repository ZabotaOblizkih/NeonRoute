from pathlib import Path

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="replace").splitlines()
for label, start, end in [("WRITE/REPLACE L863..928", 863, 928), ("RUN_SCRIPT ERR L1080..1200", 1080, 1200), ("TAIL L1400..1490", 1400, 1490)]:
    print("\n" + "=" * 80)
    print(label)
    print("=" * 80)
    for i in range(max(1, start), min(len(lines), end) + 1):
        print(f"L{i:04d}: {lines[i-1]}")
