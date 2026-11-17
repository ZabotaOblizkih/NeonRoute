from pathlib import Path

def slice_lines(rel: str, start: int, end: int):
    print("\n" + "=" * 80)
    print(f"{rel} L{start}..L{end}")
    print("=" * 80)
    lines = Path(rel).read_text(encoding="utf-8", errors="replace").splitlines()
    for i in range(max(1, start), min(len(lines), end) + 1):
        print(f"L{i:04d}: {lines[i-1]}")

slice_lines("services/engineer_diagnostics.py", 245, 300)
slice_lines("services/subagent_engineer.py", 520, 560)
slice_lines("services/subagent_engineer.py", 1280, 1400)
slice_lines("services/sandbox_manager.py", 495, 680)
slice_lines("services/code_validator.py", 320, 415)
slice_lines("services/engineer_tdd.py", 250, 335)
