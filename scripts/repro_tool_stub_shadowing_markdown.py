import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.engineer_parser import EngineerResponseParser

REPLY_WITH_STUB_TAG_AND_FULL_MD = """### ФАЗА 1: Диагноз
Нужно обновить `game.py` (например, через <<TOOL:write_file filename="game.py">>
# добавить Enemy и Coin в игровой цикл
<</TOOL:write_file>>).

### ФАЗА 2: Полная сборка
```python
import pygame
import sys

SCREEN_WIDTH, SCREEN_HEIGHT = 800, 600

class Player:
    def __init__(self, x, y):
        self.rect = pygame.Rect(x, y, 50, 50)

    def draw(self, screen):
        pygame.draw.rect(screen, (0, 255, 0), self.rect)

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    player = Player(100, 100)
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
        screen.fill((255, 255, 255))
        player.draw(screen)
        pygame.display.flip()

if __name__ == '__main__':
    main()
```

<<TOOL:run_script filename="game.py">>
"""

def main():
    print("=" * 80)
    print("T3-11 BASELINE STAND: Short Tool Tag Shadowing Full Markdown Code")
    print("=" * 80)
    with tempfile.TemporaryDirectory() as tmpdir:
        actions = EngineerResponseParser.parse_turn_actions(
            response_text=REPLY_WITH_STUB_TAG_AND_FULL_MD,
            ws_dir=Path(tmpdir),
            plan=None,
            task_prompt="Доработай game.py",
            context_hint="",
            target_artifact="game.py",
            target_entry_file="game.py",
            script_to_run="game.py",
            turn=1,
            turn1_test_signature_mismatch=False,
            verification_script=None,
            declared_entry_file="game.py",
            is_maintenance=True,
            planned_files=["game.py"],
            file_backups={"game.py": "x" * 2000},
        )
        extracted = actions.write_blocks[0][1] if actions.write_blocks else ""
        print(f"Extracted len={len(extracted)} | preview={extracted[:80]!r}")
        if len(extracted) < 300:
            print(">>> [REPRODUCED BUG-T11] Короткий тег-огрызок затмил полноценный Markdown-код (поэтому сработал Anti-Truncation Guard на 36 байт)!")
        else:
            print(">>> [PASS T11] Парсер взял полноценный код вместо огрызка из тега.")

if __name__ == "__main__":
    main()
