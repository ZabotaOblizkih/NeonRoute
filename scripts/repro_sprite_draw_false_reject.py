import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.code_validator import CodeValidator
from services.sandbox_manager import SandboxManager

SPRITE_GROUP_GAME = """import pygame
import sys

class Player(pygame.sprite.Sprite):
    def __init__(self):
        super().__init__()
        self.image = pygame.Surface((40, 40))
        self.image.fill((255, 50, 50))
        self.rect = self.image.get_rect()
        self.rect.topleft = (100, 400)
        self.vx = 4

    def update(self):
        self.rect.x += self.vx
        if self.rect.right > 700 or self.rect.left < 50:
            self.vx = -self.vx

def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    clock = pygame.time.Clock()
    all_sprites = pygame.sprite.Group()
    player = Player()
    all_sprites.add(player)

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

        all_sprites.update()
        screen.fill((20, 25, 40))
        all_sprites.draw(screen)
        pygame.display.flip()
        clock.tick(60)

if __name__ == '__main__':
    main()
"""

SINGLE_LOOP_DRAW_GAME = """import pygame
import sys

def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    clock = pygame.time.Clock()
    px = 100

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

        px += 2
        screen.fill((30, 30, 45))
        pygame.draw.rect(screen, (0, 255, 100), (px % 700, 350, 40, 40))
        pygame.display.flip()
        clock.tick(60)

if __name__ == '__main__':
    main()
"""


def main():
    print("=" * 80)
    print("T3-8 BASELINE STAND: InsufficientRenderCalls False Rejects")
    print("=" * 80)
    false_rejects = 0

    # 1. Проверка SPRITE_GROUP_GAME в CodeValidator
    v1, msg1 = CodeValidator.validate_python_syntax("game.py", SPRITE_GROUP_GAME, is_entry_file=True)
    print(f"\n[1] Sprite.Group.draw: valid={v1}")
    if not v1 and "InsufficientRenderCalls" in (msg1 or ""):
        print(f">>> [REPRODUCED REJECT 1] Ложный отказ валидатора для sprite.Group.draw: {msg1}")
        false_rejects += 1
    elif v1:
        print(">>> [PASS 1] Sprite.Group.draw валиден.")
    else:
        print(f">>> [OTHER ERROR 1]: {msg1}")

    # 2. Проверка SINGLE_LOOP_DRAW_GAME в CodeValidator
    v2, msg2 = CodeValidator.validate_python_syntax("single_draw.py", SINGLE_LOOP_DRAW_GAME, is_entry_file=True)
    print(f"\n[2] Single loop draw.rect: valid={v2}")
    if not v2 and "InsufficientRenderCalls" in (msg2 or ""):
        print(f">>> [REPRODUCED REJECT 2] Ложный отказ валидатора для одиночного draw.rect: {msg2}")
        false_rejects += 1
    elif v2:
        print(">>> [PASS 2] Single loop draw.rect валиден.")
    else:
        print(f">>> [OTHER ERROR 2]: {msg2}")

    # 3. Живой прогон Sprite.Group.draw в SandboxManager
    print("\n[3] Запуск Sprite.Group.draw в SandboxManager...")
    orig_ws = SandboxManager.get_workspace_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        SandboxManager.get_workspace_dir = classmethod(lambda cls, _p=tmp_ws: _p)
        try:
            (tmp_ws / "game.py").write_text(SPRITE_GROUP_GAME, encoding="utf-8")
            res = SandboxManager.run_script("game.py", timeout=5)
            print(f"    success={res.success}, rc={res.return_code}")
            print(f"    out={res.output.strip()[:180]!r}")
            if not res.success:
                print(">>> [FAIL 3] Smoke-тест отклонил Sprite.Group.draw!")
            else:
                print(">>> [PASS 3] Smoke-тест успешно подтвердил реальную отрисовку спрайта!")
        finally:
            SandboxManager.get_workspace_dir = orig_ws

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА ТЗ №8: ложных отказов CodeValidator {false_rejects}/2")
    print("=" * 80)


if __name__ == "__main__":
    main()
