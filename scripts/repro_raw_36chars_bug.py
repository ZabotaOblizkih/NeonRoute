import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.engineer_parser import EngineerResponseParser
from services.engineer_context import EngineerContext

ticks = chr(96) * 3
REAL_MODEL_RESPONSE = (
    "### ФАЗА 1: Краткий текстовый диагноз\n"
    "Добавляю классы Coin и Enemy и обновляю игровой цикл.\n\n"
    '### ФАЗА 2: Полная сборка монолитного файла через <<TOOL:write_file filename="game.py">>\n'
    f"{ticks}python\n"
    "import pygame\n"
    "import sys\n\n"
    "SCREEN_WIDTH, SCREEN_HEIGHT = 800, 600\n"
    "WHITE, GREEN, BLUE, RED = (255,255,255), (0,255,0), (0,0,255), (255,0,0)\n\n"
    "class Player:\n"
    "    def __init__(self, x, y):\n"
    "        self.rect = pygame.Rect(x, y, 50, 50)\n"
    "    def draw(self, screen):\n"
    "        pygame.draw.rect(screen, BLUE, self.rect)\n\n"
    "class Enemy:\n"
    "    def __init__(self, x, y):\n"
    "        self.rect = pygame.Rect(x, y, 30, 30)\n"
    "    def draw(self, screen):\n"
    "        pygame.draw.rect(screen, RED, self.rect)\n\n"
    "def main():\n"
    "    pygame.init()\n"
    "    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))\n"
    "    player = Player(100, 100)\n"
    "    enemy = Enemy(300, 300)\n"
    "    while True:\n"
    "        for ev in pygame.event.get():\n"
    "            if ev.type == pygame.QUIT:\n"
    "                pygame.quit(); sys.exit()\n"
    "        screen.fill(WHITE)\n"
    "        player.draw(screen)\n"
    "        enemy.draw(screen)\n"
    "        pygame.display.flip()\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
    f"{ticks}\n\n"
    '### Запуск главной точки входа через <<TOOL:run_script filename="game.py">>'
)

def run():
    print("=" * 80)
    print("T3-12 BASELINE STAND: Real 36-Char Truncation via Trailing ``` + Heading")
    print("=" * 80)
    bugs = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        actions = EngineerResponseParser.parse_turn_actions(
            response_text=REAL_MODEL_RESPONSE,
            ws_dir=tmp_ws,
            plan=None,
            task_prompt="Добавь врагов",
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
        print(f"[T12-1] parse_turn_actions -> len={len(extracted)} | content={extracted[:80]!r}")
        if len(extracted) < 300 or "### Запуск" in extracted:
            print(">>> [REPRODUCED BUG-T12-1] Весь код игры сжат в 36-символьный заголовок!")
            bugs += 1
        else:
            print(">>> [PASS T12-1] Извлечён полный валидный Python-код.")

    tail_trap = f"import pygame\nprint(1)\n{ticks}\n\n### Запуск главной точки входа через"
    res_pure = EngineerContext.extract_pure_py(tail_trap)
    print(f"[T12-2] extract_pure_py(tail_trap) -> {res_pure!r}")
    if not EngineerContext.has_python_code(res_pure):
        print(">>> [REPRODUCED BUG-T12-2] extract_pure_py принял текст после закрывающих кавычек за код!")
        bugs += 1
    else:
        print(">>> [PASS T12-2] extract_pure_py сохранил Python-код.")

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА ТЗ №12: воспроизведено багов {bugs}/2")
    print("=" * 80)

if __name__ == "__main__":
    run()
