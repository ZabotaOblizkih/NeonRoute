from pathlib import Path

ROOT = Path(".")

def find_block(rel: str, needle: str, before: int = 10, after: int = 55):
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

find_block("services/sandbox_manager.py", "Экран полностью статичен", before=45, after=25)
find_block("services/subagent_engineer.py", "Анализ причины падения", before=40, after=35)
find_block("services/engineer_parser.py", "Перехвачен чистый Python-код", before=25, after=25)
