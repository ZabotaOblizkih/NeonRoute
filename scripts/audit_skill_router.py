from pathlib import Path

print("=== engineer_context.py L105..140 ===")
ec = Path("services/engineer_context.py").read_text(encoding="utf-8", errors="replace").splitlines()
for i in range(105, min(len(ec), 140) + 1):
    print(f"L{i:03d}: {ec[i-1]}")

print("\n=== engineer_skill_router.py (skills + route logic) ===")
sr = Path("services/engineer_skill_router.py").read_text(encoding="utf-8", errors="replace").splitlines()
for i, line in enumerate(sr, 1):
    if i >= 160:
        print(f"L{i:03d}: {line}")
