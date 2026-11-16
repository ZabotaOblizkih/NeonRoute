import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.engineer_diagnostics import diagnose_runtime_error
from services.code_validator import CodeValidator
from services.sandbox_manager import SandboxManager

TK_EMPTY = """import tkinter as tk
root = tk.Tk()
root.title("Empty")
root.mainloop()
"""

TK_UNPACKED = """import tkinter as tk
root = tk.Tk()
lbl = tk.Label(root, text="Hello")
btn = tk.Button(root, text="OK")
root.mainloop()
"""

TK_GOOD = """import tkinter as tk
root = tk.Tk()
lbl = tk.Label(root, text="Score: 0")
lbl.pack()
btn = tk.Button(root, text="Add", command=lambda: lbl.config(text="Score: 1"))
btn.pack()
root.mainloop()
"""

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

PYGAME_GOOD = """import pygame, sys
pygame.init()
screen = pygame.display.set_mode((800, 600))
clock = pygame.time.Clock()
px = 100
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

PYGAME_EMPTY_HANDLE = """import pygame, sys

class Level:
    def handle_collision(self, player):
        pass

    def draw(self, scr):
        pygame.draw.rect(scr, (255, 255, 255), (0, 500, 800, 100))

def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    clock = pygame.time.Clock()
    lvl = Level()
    x = 100
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
        lvl.handle_collision(None)
        screen.fill((0, 0, 0))
        lvl.draw(screen)
        pygame.draw.rect(screen, (255, 0, 0), (x, 450, 40, 40))
        pygame.display.flip()
        clock.tick(60)

if __name__ == '__main__':
    main()
"""


def main():
    print("=" * 80)
    print("WAVE 1 BASELINE STAND: Diagnostics, GUI Sensors & Stub Guard")
    print("=" * 80)
    bugs = 0

    # --- 1. diagnose_runtime_error: ModuleNotFoundError + pygame.error + TypeError ---
    d_mod = diagnose_runtime_error("ModuleNotFoundError: No module named 'nonexistent_pkg'")
    d_pg = diagnose_runtime_error("pygame.error: Couldn't open mario.png")
    d_type = diagnose_runtime_error("TypeError: Player.update() missing 1 required positional argument: 'clock'")

    bad_mod_advice = "Создай файл 'nonexistent_pkg.py'" in d_mod
    no_pg_hint = "РЕКОМЕНДАЦИИ" not in d_pg
    no_type_hint = "РЕКОМЕНДАЦИИ" not in d_type
    print(f"\n[W1-1] Diagnostics: bad_mod_advice={bad_mod_advice}, no_pg_hint={no_pg_hint}, no_type_hint={no_type_hint}")
    if bad_mod_advice or no_pg_hint or no_type_hint:
        print(">>> [REPRODUCED BUG-W1-1] ModuleNotFoundError толкает плодить фейк-файлы, а pygame.error/TypeError идут без подсказок!")
        bugs += 1
    else:
        print(">>> [PASS W1-1] Диагностика ошибок корректна и покрывает pygame.error/TypeError.")

    orig_get_ws = SandboxManager.get_workspace_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        SandboxManager.get_workspace_dir = classmethod(lambda cls, _p=tmp_ws: _p)
        try:
            # --- 2. Tkinter пустое / неразмещённое окно ---
            (tmp_ws / "tk_empty.py").write_text(TK_EMPTY, encoding="utf-8")
            (tmp_ws / "tk_unpacked.py").write_text(TK_UNPACKED, encoding="utf-8")
            (tmp_ws / "tk_good.py").write_text(TK_GOOD, encoding="utf-8")
            r_tk_empty = SandboxManager.run_script("tk_empty.py")
            r_tk_unpack = SandboxManager.run_script("tk_unpacked.py")
            r_tk_good = SandboxManager.run_script("tk_good.py")
            print(f"\n[W1-2] Tkinter: empty={r_tk_empty.success}, unpacked={r_tk_unpack.success}, good={r_tk_good.success}")
            if r_tk_empty.success or r_tk_unpack.success:
                print(">>> [REPRODUCED BUG-W1-2] Пустое/неразмещённое окно Tkinter прошло Smoke-тест как успех!")
                bugs += 1
            elif r_tk_good.success:
                print(">>> [PASS W1-2] Пустые/неразмещённые Tkinter окна отклоняются, рабочее окно проходит.")
            else:
                print(f">>> [FAIL W1-2] Рабочее Tkinter окно не прошло: {r_tk_good.stderr}")

            # --- 3. Pygame без display.flip / update ---
            (tmp_ws / "pg_no_flip.py").write_text(PYGAME_NO_FLIP, encoding="utf-8")
            r_no_flip = SandboxManager.run_script("pg_no_flip.py")
            print(f"\n[W1-3] Pygame без flip/update: success={r_no_flip.success}, rc={r_no_flip.return_code}")
            if r_no_flip.success:
                print(">>> [REPRODUCED BUG-W1-3] Pygame без вывода кадров на экран прошёл SandboxManager с success=True!")
                bugs += 1
            else:
                print(">>> [PASS W1-3] Pygame без flip/update отклонён.")

            # --- 4. Pygame игрок за экраном: наличие фактов палитры в ошибке ---
            (tmp_ws / "pg_offscreen.py").write_text(PYGAME_OFFSCREEN, encoding="utf-8")
            (tmp_ws / "pg_good.py").write_text(PYGAME_GOOD, encoding="utf-8")
            r_off = SandboxManager.run_script("pg_offscreen.py")
            r_good = SandboxManager.run_script("pg_good.py")
            err_off = f"{r_off.stderr}\n{r_off.output}"
            has_palette = "(20, 20, 40)" in err_off and "%" in err_off
            print(f"\n[W1-4] Pygame offscreen: success={r_off.success}, has_palette={has_palette} | good={r_good.success}")
            if (not r_off.success) and (not has_palette):
                print(">>> [REPRODUCED BUG-W1-4] Ошибка статичного экрана не содержит реальную палитру цветов экрана!")
                bugs += 1
            elif (not r_off.success) and has_palette and r_good.success:
                print(">>> [PASS W1-4] Ошибка содержит реальный срез палитры, рабочая игра проходит.")
            else:
                print(f">>> [FAIL W1-4] Проверить good={r_good.success}: {r_good.stderr}")
        finally:
            SandboxManager.get_workspace_dir = orig_get_ws

    # --- 5. CodeValidator AST Stub Guard для handle_* ---
    ok_stub, msg_stub = CodeValidator.validate_python_syntax("game.py", PYGAME_EMPTY_HANDLE, is_entry_file=True)
    print(f"\n[W1-5] CodeValidator на пустом handle_collision(): valid={ok_stub} | msg={msg_stub}")
    if ok_stub:
        print(">>> [REPRODUCED BUG-W1-5] Пустой метод def handle_collision(...): pass пропущен валидатором!")
        bugs += 1
    else:
        print(">>> [PASS W1-5] Пустой метод handle_collision отклонён валидатором.")

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА ВОЛНЫ 1: воспроизведено багов {bugs}/5")
    print("=" * 80)


if __name__ == "__main__":
    main()
