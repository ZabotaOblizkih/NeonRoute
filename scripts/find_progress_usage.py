from pathlib import Path

for p in Path("ui").rglob("*.py"):
    try:
        content = p.read_text(encoding="utf-8")
        if "ProgressRing" in content or "ProgressBar" in content or "Indeterminate" in content:
            print(f"[+] Найдено в {p}:")
            for idx, line in enumerate(content.splitlines()):
                if any(k in line for k in ["ProgressRing", "ProgressBar", "Indeterminate"]):
                    print(f"  {idx+1:4d}: {line.strip()}")
    except Exception:
        pass
