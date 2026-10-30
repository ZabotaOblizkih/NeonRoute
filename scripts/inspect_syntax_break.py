from pathlib import Path
se_lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, line in enumerate(se_lines):
    if "Извлеченный код содержит синтаксическую ошибку" in line:
        start = max(0, idx - 5)
        end = min(len(se_lines), idx + 25)
        print(f"=== services/subagent_engineer.py (строки {start+1}-{end}) ===")
        for j in range(start, end):
            print(f"{j+1:4d} | {se_lines[j]}")
        print("=" * 60)
