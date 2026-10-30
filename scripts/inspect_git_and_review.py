from pathlib import Path

print("=== 1. services/subagent_engineer.py: ветка review ===")
se_lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(se_lines):
    if "review" in l.lower():
        for j in range(max(0, idx - 3), min(len(se_lines), idx + 20)):
            print(f"{j+1:4d} | {se_lines[j]}")
        break

print("\n=== 2. services/git_sandbox.py: методы коммита ===")
gs_lines = Path("services/git_sandbox.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(gs_lines):
    if any(k in l for k in ["def commit", "def create_checkpoint", "def save"]):
        for j in range(idx, min(len(gs_lines), idx + 12)):
            print(f"{j+1:4d} | {gs_lines[j]}")
