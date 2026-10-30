from pathlib import Path

print("=== 1. services/subagent_engineer.py (строки 380-440: логика review) ===")
se_lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for i in range(380, min(440, len(se_lines))):
    print(f"{i+1:4d} | {se_lines[i]}")

print("\n=== 2. services/git_sandbox.py (метод checkpoint) ===")
gs_lines = Path("services/git_sandbox.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(gs_lines):
    if "def checkpoint" in l:
        for j in range(idx, min(idx + 25, len(gs_lines))):
            print(f"{j+1:4d} | {gs_lines[j]}")
        break
