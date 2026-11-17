from pathlib import Path

def slice_lines(rel: str, start: int, end: int):
    print(f"\n=== {rel} L{start}..L{end} ===")
    lines = Path(rel).read_text(encoding="utf-8", errors="replace").splitlines()
    for i in range(max(1, start), min(len(lines), end) + 1):
        print(f"L{i:04d}: {lines[i-1]}")

slice_lines("services/engineer_parser.py", 50, 125)
slice_lines("services/engineer_parser.py", 315, 390)

cv = Path("services/code_validator.py").read_text(encoding="utf-8", errors="replace").splitlines()
for i, l in enumerate(cv, 1):
    if "def validate_code_integrity" in l:
        slice_lines("services/code_validator.py", i, i + 65)
        break
