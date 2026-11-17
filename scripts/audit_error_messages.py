import ast
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.engineer_diagnostics import diagnose_runtime_error, extract_error_fingerprint
from services.code_validator import CodeValidator
from services.sandbox_manager import SandboxManager
from services.tool_engine import ToolEngine

def header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

# 1. Полный разбор engineer_diagnostics.py
header("1. services/engineer_diagnostics.py (diagnose_runtime_error & fingerprint)")
diag_src = (ROOT / "services" / "engineer_diagnostics.py").read_text(encoding="utf-8", errors="replace")
for i, line in enumerate(diag_src.splitlines(), 1):
    if any(k in line for k in ("def ", "if ", "elif ", "return ")):
        print(f"L{i:03d}: {line}")

# 2. Каталог всех сообщений об ошибках в ключевых модулях (через AST)
def extract_error_strings(rel_path: str):
    p = ROOT / rel_path
    if not p.exists():
        return []
    src = p.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    found = []
    keywords = ("ошибк", "сбой", "забраков", "запрещ", "не найден", "отсутств", "уменьш", "error", "invalid", "fail", "missing", "static", "статич", "однотон", "цикл", "loop")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value.strip()
            if len(val) >= 12 and any(k in val.lower() for k in keywords):
                line_no = getattr(node, "lineno", 0)
                one_line = val.replace("\n", " \\n ")
                if len(one_line) > 150:
                    one_line = one_line[:147] + "..."
                found.append((line_no, one_line))
    # Убираем дубли, сортируем по строкам
    seen = set()
    uniq = []
    for ln, txt in sorted(found):
        if (ln, txt) not in seen:
            seen.add((ln, txt))
            uniq.append((ln, txt))
    return uniq

for mod in (
    "services/code_validator.py",
    "services/tool_engine.py",
    "services/sandbox_manager.py",
    "services/subagent_engineer.py",
    "services/engineer_tdd.py",
):
    header(f"2. Диагностические сообщения в {mod}")
    items = extract_error_strings(mod)
    for ln, txt in items[:45]:
        print(f"L{ln:04d}: {txt}")
    if len(items) > 45:
        print(f"... и ещё {len(items) - 45} строк")

# 3. Реальная симуляция: что уходит в LLM при 6 частых поломках
header("3. СИМУЛЯЦИЯ РЕАЛЬНЫХ СЦЕНАРИЕВ (что буквально видит модель)")

# Сценарий A: Tkinter с пустым окном (без виджетов)
TK_EMPTY = """import tkinter as tk
root = tk.Tk()
root.title("Empty")
root.mainloop()
"""

# Сценарий B: Tkinter кнопка падает с ошибкой при нажатии
TK_BTN_CRASH = """import tkinter as tk
def on_click():
    x = 1 / 0
root = tk.Tk()
btn = tk.Button(root, text="Calc", command=on_click)
btn.pack()
root.mainloop()
"""

# Сценарий C: Pygame игрок за пределами экрана (x=1200 при ширине 800)
PYGAME_OFFSCREEN = """import pygame, sys
pygame.init()
screen = pygame.display.set_mode((800, 600))
clock = pygame.time.Clock()
px = 1200
while True:
    for ev in pygame.event.get():
        if ev.type == pygame.QUIT:
            pygame.quit(); sys.exit()
    keys = pygame.key.get_pressed()
    if keys[pygame.K_RIGHT]:
        px += 5
    screen.fill((20, 20, 40))
    pygame.draw.rect(screen, (255, 255, 255), (100, 500, 200, 30))
    pygame.draw.rect(screen, (255, 0, 0), (px, 450, 40, 40))
    pygame.display.flip()
    clock.tick(60)
"""

# Сценарий D: Pygame без display.flip()/update()
PYGAME_NO_FLIP = """import pygame, sys
pygame.init()
screen = pygame.display.set_mode((800, 600))
clock = pygame.time.Clock()
px = 100
while True:
    for ev in pygame.event.get():
        if ev.type == pygame.QUIT:
            pygame.quit(); sys.exit()
    px += 5
    screen.fill((20, 20, 40))
    pygame.draw.rect(screen, (255, 0, 0), (px, 450, 40, 40))
    clock.tick(60)
"""

orig_get_ws = SandboxManager.get_workspace_dir
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_ws = Path(tmpdir)
    SandboxManager.get_workspace_dir = classmethod(lambda cls, _p=tmp_ws: _p)
    try:
        cases = [
            ("A. Tkinter пустое окно", "tk_empty.py", TK_EMPTY),
            ("B. Tkinter кнопка с ZeroDivisionError", "tk_crash.py", TK_BTN_CRASH),
            ("C. Pygame игрок за границами экрана (x=1200)", "pg_offscreen.py", PYGAME_OFFSCREEN),
            ("D. Pygame забыт display.flip/update", "pg_no_flip.py", PYGAME_NO_FLIP),
        ]
        for label, fname, code in cases:
            print(f"\n--- {label} ---")
            ok_st, st_msg = CodeValidator.validate_python_syntax(fname, code, is_entry_file=True)
            print(f"[CodeValidator] valid={ok_st} | msg={st_msg}")
            (tmp_ws / fname).write_text(code, encoding="utf-8")
            res = SandboxManager.run_script(fname)
            raw_err = res.stderr or res.output
            diag = diagnose_runtime_error(raw_err)
            fp = extract_error_fingerprint(raw_err)
            print(f"[Sandbox] success={res.success}, rc={res.return_code}")
            print(f"[RAW -> LLM]: {raw_err.strip()!r}")
            print(f"[diagnose_runtime_error]: {diag.strip()!r}")
            print(f"[fingerprint]: {fp!r}")

        # Сценарий E: ToolEngine.patch_file при несовпадении SEARCH
        print("\n--- E. ToolEngine.patch_file не нашел фрагмент ---")
        (tmp_ws / "sample.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
        patch_res = ToolEngine.patch_file("sample.py", "def missing_fn():", "def other():")
        print(f"[ToolEngine.patch_file] success={patch_res.success} | output={patch_res.output!r}")

        # Сценарий F: diagnose_runtime_error для частых Python-исключений
        print("\n--- F. diagnose_runtime_error на типовых traceback ---")
        for err_sample in (
            "Traceback (most recent call last):\n  File \"game.py\", line 12, in <module>\n    import nonexistent_pkg\nModuleNotFoundError: No module named 'nonexistent_pkg'",
            "Traceback (most recent call last):\n  File \"make_doc.py\", line 44, in <module>\n    wb.save('report.xlsx')\nPermissionError: [Errno 13] Permission denied: 'report.xlsx'",
            "Traceback (most recent call last):\n  File \"app.py\", line 19, in update\n    player.update(keys)\nTypeError: Player.update() missing 1 required positional argument: 'clock'",
        ):
            print(f"  IN : {err_sample.splitlines()[-1]}")
            print(f"  OUT: {diagnose_runtime_error(err_sample).strip()!r}")
    finally:
        SandboxManager.get_workspace_dir = orig_get_ws
