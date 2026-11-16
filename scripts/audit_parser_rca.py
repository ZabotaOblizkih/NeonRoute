from pathlib import Path

ROOT = Path(".")

def find_block(rel: str, needle: str, before: int = 5, after: int = 55):
    print("\n" + "=" * 80)
    print(f"{rel} -> '{needle}'")
    print("=" * 80)
    lines = (ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines, 1):
        if needle in line:
            for j in range(max(1, i - before), min(len(lines), i + after) + 1):
                print(f"L{j:04d}: {lines[j-1]}")
            return
    print("[NOT FOUND]")

find_block("services/engineer_context.py", "def extract_pure_py", before=2, after=65)
find_block("services/subagent_engineer.py", "SubagentEngineer: сбой в", before=50, after=15)
