from pathlib import Path
import re

def search_in_files(dir_path: Path, patterns: list):
    for p in dir_path.rglob("*.py"):
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            for pat in patterns:
                if re.search(pat, content, re.IGNORECASE):
                    print(f"=== {p.as_posix()} (найдено: {pat}) ===")
                    lines = content.splitlines()
                    for idx, line in enumerate(lines):
                        if re.search(pat, line, re.IGNORECASE):
                            start = max(0, idx - 2)
                            end = min(len(lines), idx + 5)
                            print(f"--- Строка {idx+1} ---")
                            for j in range(start, end):
                                m = ">>>" if j == idx else "   "
                                print(f"{j+1:4d} {m} | {lines[j]}")
                            break
        except Exception:
            pass

print("=== 1. Поиск восстановления сессии при старте ===")
search_in_files(Path("ui"), [
    r"restore_last_session",
    r"load_session",
    r"initial_session",
    r"workspace_inspector.*(?:show|setVisible|hide)"
])

print("\n=== 2. Поиск связи ModeCoordinator с инспектором ===")
search_in_files(Path("core"), [
    r"class ModeCoordinator",
    r"switch_mode"
])
