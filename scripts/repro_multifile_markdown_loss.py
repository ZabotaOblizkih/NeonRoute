import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.engineer_parser import EngineerResponseParser
from services.engineer_diagnostics import extract_error_fingerprint

MULTIFILE_MARKDOWN_REPLY = """Вот готовая игра с разделением на модули:

**player.py**
```python
import pygame

class Player:
    def __init__(self, x, y):
        self.rect = pygame.Rect(x, y, 40, 40)

    def update(self, dt, keys):
        if keys[pygame.K_RIGHT]:
            self.rect.x += 5

    def draw(self, screen):
        pygame.draw.rect(screen, (0, 0, 255), self.rect)
```

**game.py**
```python
import pygame
import sys
from player import Player

def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    clock = pygame.time.Clock()
    player = Player(100, 400)
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
        player.update(0.016, pygame.key.get_pressed())
        screen.fill((255, 255, 255))
        player.draw(screen)
        pygame.display.flip()
        clock.tick(60)

if __name__ == '__main__':
    main()
```
"""

MULTIFILE_COLON_REPLY = """```python:player.py
class Player:
    pass
```

```python:game.py
from player import Player
print(Player())
```
"""

MAINTENANCE_BEFORE_AFTER = """Вот как выглядел старый код с ошибкой:
```python
def run():
    print("Old buggy code")
```

А вот исправленная версия:
```python
def run():
    print("Fixed code")
```
"""

def main():
    print("=" * 80)
    print("T3-9 BASELINE STAND: Multifile Markdown Drop & ModuleNotFoundError Fingerprint")
    print("=" * 80)
    bugs = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        for label, resp in [("Markdown headers (**player.py**)", MULTIFILE_MARKDOWN_REPLY), ("Colon syntax (python:player.py)", MULTIFILE_COLON_REPLY)]:
            actions = EngineerResponseParser.parse_turn_actions(
                response_text=resp,
                ws_dir=tmp_ws,
                plan=None,
                task_prompt="Сделай игру на Pygame",
                context_hint="",
                target_artifact="game.py",
                target_entry_file="game.py",
                script_to_run="game.py",
                turn=1,
                turn1_test_signature_mismatch=False,
                verification_script=None,
                declared_entry_file="game.py",
                is_maintenance=False,
                planned_files=["game.py"],
                file_backups={},
            )
            names = [fn for fn, _ in actions.write_blocks]
            print(f"\n[{label}] extracted files = {names}")
            if "player.py" not in names or "game.py" not in names:
                print(f">>> [REPRODUCED BUG-T9-1] Многофайловый ответ потерял файлы (осталось {names})!")
                bugs += 1
            else:
                print(">>> [PASS T9-1] Оба файла успешно извлечены.")

        orig_code = 'def run():\n    print("Old buggy code")\n'
        (tmp_ws / "app.py").write_text(orig_code, encoding="utf-8")
        actions_maint = EngineerResponseParser.parse_turn_actions(
            response_text=MAINTENANCE_BEFORE_AFTER,
            ws_dir=tmp_ws,
            plan=None,
            task_prompt="Исправь ошибку в app.py",
            context_hint="",
            target_artifact="app.py",
            target_entry_file="app.py",
            script_to_run="app.py",
            turn=2,
            turn1_test_signature_mismatch=False,
            verification_script=None,
            declared_entry_file="app.py",
            is_maintenance=True,
            planned_files=["app.py"],
            file_backups={"app.py": orig_code},
        )
        names_maint = [fn for fn, _ in actions_maint.write_blocks]
        print(f"\n[Maintenance before/after] extracted files = {names_maint}")
        if names_maint != ["app.py"]:
            print(f">>> [FAIL MAINTENANCE] Ожидали ровно ['app.py'], получили {names_maint}!")
            bugs += 1
        elif "Fixed code" not in actions_maint.write_blocks[0][1]:
            print(">>> [FAIL MAINTENANCE] Взят не новый код!")
            bugs += 1
        else:
            print(">>> [PASS MAINTENANCE] Взят ровно 1 файл app.py с обновлённым кодом.")

    fp1 = extract_error_fingerprint('  File "C:\\ws\\game.py", line 3, in <module>\nModuleNotFoundError: No module named \'player\'')
    fp2 = extract_error_fingerprint('  File "C:\\ws\\game.py", line 4, in <module>\nModuleNotFoundError: No module named \'player\'')
    print(f"\n[Fingerprint line-shift] fp1={fp1!r}, fp2={fp2!r}")
    if fp1 != fp2:
        print(">>> [REPRODUCED BUG-T9-2] Сдвиг строки импорта на 1 строку сбросил счётчик зацикливания!")
        bugs += 1
    else:
        print(">>> [PASS T9-2] Отпечаток ModuleNotFoundError стабилен при сдвиге строки.")

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА ТЗ №9: воспроизведено багов {bugs}/3")
    print("=" * 80)

if __name__ == "__main__":
    main()
