from pathlib import Path

ROOT = Path(".")

def slice_lines(rel: str, start: int, end: int):
    print("\n" + "=" * 80)
    print(f"{rel} L{start}..L{end}")
    print("=" * 80)
    lines = (ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    for i in range(max(1, start), min(len(lines), end) + 1):
        print(f"L{i:04d}: {lines[i-1]}")

slice_lines("services/engineer_planner.py", 290, 520)
slice_lines("services/subagent_engineer.py", 330, 445)
slice_lines("services/subagent_engineer.py", 735, 830)
slice_lines("services/sandbox_manager.py", 340, 465)
