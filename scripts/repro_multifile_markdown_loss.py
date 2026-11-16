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
