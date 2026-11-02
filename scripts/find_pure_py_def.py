from pathlib import Path

print("[*] Глобальный поиск def _extract_pure_py по всему проекту:")
for p in Path(".").glob("**/*.py"):
    if any(part in p.parts for part in (".git", ".venv", "venv", "__pycache__")):
        continue
    try:
        txt = p.read_text(encoding="utf-8")
        if "def _extract_pure_py" in txt:
            print(f"    [НАЙДЕНО] {p}")
            for idx, line in enumerate(txt.splitlines()):
                if "def _extract_pure_py" in line:
                    print(f"        Строка {idx+1}: {line.strip()}")
    except Exception:
        pass
