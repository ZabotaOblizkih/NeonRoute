import sys
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

for folder_name in ["tests", "test", "scripts"]:
    t_dir = PROJECT_ROOT / folder_name
    if not t_dir.exists():
        continue
    print(f"\n=== ДИРЕКТОРИЯ: {folder_name}/ ===")
    total_files = 0
    total_funcs = 0
    total_lines = 0
    rows = []
    for p in sorted(t_dir.rglob("*.py")):
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
            lines = len(src.splitlines())
            tree = ast.parse(src)
            test_funcs = [
                n.name for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
            ]
            # Оценим долю моков/заглушек
            mock_count = src.count("MagicMock") + src.count("patch(") + src.count("monkeypatch")
            rel = p.relative_to(PROJECT_ROOT)
            rows.append((str(rel), len(test_funcs), lines, mock_count))
            total_files += 1
            total_funcs += len(test_funcs)
            total_lines += lines
        except Exception as e:
            rows.append((str(p.relative_to(PROJECT_ROOT)), -1, 0, 0))

    print(f"{'Файл':<52} | {'Тестов':>6} | {'Строк':>6} | {'Моков':>5}")
    print("-" * 78)
    for rel, t_cnt, l_cnt, m_cnt in rows:
        print(f"{rel:<52} | {t_cnt:>6} | {l_cnt:>6} | {m_cnt:>5}")
    print("-" * 78)
    print(f"ИТОГО в {folder_name}/: {total_files} файлов, {total_funcs} тестовых функций, {total_lines} строк")
