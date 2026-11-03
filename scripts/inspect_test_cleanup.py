import sys
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

print("=== ТЕКУЩИЙ tests/conftest.py ===")
conftest_path = PROJECT_ROOT / "tests" / "conftest.py"
if conftest_path.exists():
    print(conftest_path.read_text(encoding="utf-8", errors="ignore"))

print("\n=== КАНДИДАТЫ НА УДАЛЕНИЕ В tests/ (Одноразовые патчи, 0-тестовые скрипты, дубли, легаси) ===")
candidates = []
for p in sorted((PROJECT_ROOT / "tests").rglob("*.py")):
    if p.name in ("__init__.py", "conftest.py") or "smoke" in p.parts:
        continue
    raw = p.read_bytes()
    text = raw.decode("utf-8", errors="ignore").lstrip("\ufeff")
    lines = len(text.splitlines())
    try:
        tree = ast.parse(text)
        t_funcs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")]
    except Exception:
        t_funcs = []
    mocks = text.count("MagicMock") + text.count("patch(") + text.count("monkeypatch")
    rel = str(p.relative_to(PROJECT_ROOT))

    reason = None
    if len(t_funcs) == 0:
        reason = "0 функций test_* (не запускается pytest)"
    elif any(k in p.name for k in ["patch_", "patches_", "refactoring", "step12", "v087", "depth1_", "gemini4argon", "mobile_"]):
        reason = "Одноразовый патч-тест старого спринта / легаси"
    elif mocks > 25 and mocks > len(t_funcs) * 2.5:
        reason = f"Перегружен моками ({mocks} моков на {len(t_funcs)} тестов) — тестирует заглушки"
    elif len(t_funcs) <= 3 and lines < 160:
        reason = f"Микро-тест ({len(t_funcs)} шт.) — полностью перекрывается смок-тестами"

    if reason:
        candidates.append((rel, len(t_funcs), lines, mocks, reason))

for rel, tf, ln, mk, r in candidates:
    print(f"  - {rel:<52} | {tf:>2} тестов | {ln:>3} стр. | {r}")
print(f"\nИтого кандидатов на очистку в tests/: {len(candidates)} файлов")
