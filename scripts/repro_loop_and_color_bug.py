import inspect
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.engineer_context import EngineerContext
from services.engineer_parser import EngineerResponseParser
from services.sandbox_manager import SandboxManager

RUSSIAN_CHATTER_REPLY = "Конечно, исправлю ошибку и верну полный обновленный файл `game.py`."

GREEN_ON_GREEN_GAME = """import pygame
import sys

pygame.init()
SCREEN_WIDTH, SCREEN_HEIGHT = 800, 600
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))

class Player:
    def __init__(self, x, y):
        self.rect = pygame.Rect(x, y, 50, 50)
        self.vy = 0

    def update(self, keys):
        if keys[pygame.K_LEFT]:
            self.rect.x -= 5
        if keys[pygame.K_RIGHT]:
            self.rect.x += 5

    def draw(self, scr):
        pygame.draw.rect(scr, GREEN, self.rect)

class Level:
    def __init__(self):
        self.platforms = [pygame.Rect(100, 550, 200, 50)]

    def draw(self, scr):
        for p in self.platforms:
            pygame.draw.rect(scr, WHITE, p)

def main():
    clock = pygame.time.Clock()
    player = Player(400, 500)
    level = Level()
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
        player.update(pygame.key.get_pressed())
        screen.fill(GREEN)
        level.draw(screen)
        player.draw(screen)
        pygame.display.flip()
        clock.tick(60)

if __name__ == '__main__':
    main()
"""

def call_parse_turn_actions(response_text: str, tmp_ws: Path):
    fn = EngineerResponseParser.parse_turn_actions
    sig = inspect.signature(fn)
    known = {
        "response_text": response_text,
        "raw_response": response_text,
        "text": response_text,
        "ws_dir": tmp_ws,
        "target_artifact": "game.py",
        "target_entry_file": "game.py",
        "is_maintenance": True,
        "file_backups": {},
        "planned_files": ["game.py"],
        "is_analysis": False,
        "turn": 2,
    }
    kwargs = {}
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        if pname in known:
            kwargs[pname] = known[pname]
        elif param.default is inspect.Parameter.empty:
            kwargs[pname] = None
    return fn(**kwargs)

def main():
    print("=" * 80)
    print("BASELINE REPRODUCTION STAND #2: Chatter-as-Python & Color-Merge Hint")
    print("=" * 80)
    bugs = 0

    # --- BUG-4: Русская фраза без кода перехватывается как Python-файл ---
    has_py = EngineerContext.has_python_code(RUSSIAN_CHATTER_REPLY)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        actions = call_parse_turn_actions(RUSSIAN_CHATTER_REPLY, tmp_ws)
        print(f"\n[4] Русский ответ без кода: has_python_code={has_py}, write_blocks={len(actions.write_blocks)}")
        if actions.write_blocks:
            print(f"    Перехвачено как файл: {actions.write_blocks[0][0]} -> {actions.write_blocks[0][1]!r}")
            print(">>> [REPRODUCED BUG-4] Текстовый ответ попал в write_blocks (даёт SyntaxError и сбрасывает счётчик зацикливания)!")
            bugs += 1
        else:
            print(">>> [PASS BUG-4] Болтовня без кода не попала в write_blocks.")

    # --- BUG-5: Зелёный игрок на зелёном фоне — нет подсказки о слиянии цветов ---
    orig_get_ws = SandboxManager.get_workspace_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        SandboxManager.get_workspace_dir = classmethod(lambda cls, _p=tmp_ws: _p)
        try:
            (tmp_ws / "game.py").write_text(GREEN_ON_GREEN_GAME, encoding="utf-8")
            res = SandboxManager.run_script("game.py")
            err_text = f"{res.stderr}\n{res.output}"
            has_color_hint = any(k in err_text.lower() for k in ("совпадает с цветом фона", "контраст", "слил"))
            print(f"\n[5] Зелёный игрок на зелёном фоне -> success={res.success}, rc={res.return_code}")
            print(f"    Сообщение: {res.stderr.strip()!r}")
            if (not res.success) and (not has_color_hint):
                print(">>> [REPRODUCED BUG-5] Тест поймал слияние, но не подсказал модели проверить совпадение цвета игрока и фона!")
                bugs += 1
            elif has_color_hint:
                print(">>> [PASS BUG-5] Есть явная диагностика совпадения цвета игрока и фона.")
        finally:
            SandboxManager.get_workspace_dir = orig_get_ws

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА #2: воспроизведено багов {bugs}/2")
    print("=" * 80)

if __name__ == "__main__":
    main()
