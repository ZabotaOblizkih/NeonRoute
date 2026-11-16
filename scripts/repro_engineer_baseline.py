import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Приглушаем фоновый шум, чтобы видеть чистый отчет стенда
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.engineer_context import EngineerContext
from services.engineer_planner import EngineerPlanner
from services.engineer_skill_router import EngineerSkillRouter
from services.code_validator import CodeValidator
from services.tool_engine import ToolEngine
from services.sandbox_manager import SandboxManager

MARIO_BROKEN_CODE = """import pygame

class Mario:
    def __init__(self):
        self.x = 100
        self.y = 100
        self.speed = 5

    def move(self, direction):
        if direction == 'left':
            self.x -= self.speed
        elif direction == 'right':
            self.x += self.speed
        elif direction == 'up':
            self.y -= self.speed
        elif direction == 'down':
            self.y += self.speed

    def jump(self):
        self.y -= self.speed * 2
        pygame.time.delay(100)
        self.y += self.speed * 2


class Level:
    def __init__(self, layout):
        self.layout = layout

    def draw(self, screen):
        for y, row in enumerate(self.layout):
            for x, tile in enumerate(row):
                if tile == '1':
                    pygame.draw.rect(screen, (0, 0, 255), (x * 50, y * 50, 50, 50))


def main_loop(screen, mario, level):
    running = True
    clock = pygame.time.Clock()
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    mario.move('left')
                elif event.key == pygame.K_RIGHT:
                    mario.move('right')
                elif event.key == pygame.K_UP:
                    mario.jump()

        screen.fill((255, 255, 255))
        mario.move('down')
        level.draw(screen)
        pygame.display.update()
        clock.tick(60)

    pygame.quit()


if __name__ == '__main__':
    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    mario = Mario()
    level = Level([['1', '1', '1'], ['1', '0', '1'], ['1', '1', '1']])
    main_loop(screen, mario, level)
"""

MARIO_NO_MAIN = MARIO_BROKEN_CODE.split("if __name__ == '__main__':")[0]

EMPTY_PYGAME_STUB = """import pygame

def run_game():
    pygame.init()
    screen = pygame.display.set_mode((400, 300))
    clock = pygame.time.Clock()
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
        screen.fill((0, 0, 0))
        clock.tick(60)

if __name__ == '__main__':
    run_game()
"""


class FakeArchitectClient:
    """Возвращает ровно тот JSON, который выдал Архитектор в логе."""
    def chat_completion(self, **kwargs):
        return """{
  "task_type": "NEW_MODULE",
  "target_file": "mario_core.py",
  "target_artifact": "mario_core.py",
  "format": "interactive_loop",
  "intent": "new",
  "interface_spec": {
    "classes": [
      {"name": "Mario", "methods": [{"name": "__init__", "args": ["self"]}, {"name": "move", "args": ["self", "direction"]}, {"name": "jump", "args": ["self"]}]},
      {"name": "Level", "methods": [{"name": "__init__", "args": ["self", "layout"]}, {"name": "draw", "args": ["self", "screen"]}]}
    ],
    "functions": [{"name": "main_loop", "args": ["screen", "mario", "level"]}]
  },
  "plan": "Разработка простой игры Марио с интерактивным игровым циклом."
}"""


def main():
    print("=" * 80)
    print("BASELINE REPRODUCTION STAND: NeonRoute Engineer Pipeline")
    print("=" * 80)

    bugs_reproduced = 0

    # --- BUG-1: Потеря формата и затирание принудительного скилла ---
    norm_fmt = EngineerContext.normalize_artifact_format(
        "interactive_loop", "Сделай простую игру марио", has_verification_script=False
    )
    print(f"\n[1A] normalize_artifact_format('interactive_loop', 'Сделай простую игру марио') -> '{norm_fmt}'")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        plan = EngineerPlanner.plan_architecture(
            task_prompt="Сделай простую игру марио",
            ws_dir=tmp_ws,
            client=FakeArchitectClient(),
            target_model="fake-coder",
            active_engineer_skill="interactive_loop",
        )
        print(f"[1B] ArchitectPlan при active_engineer_skill='interactive_loop':")
        print(f"     artifact_format = '{plan.artifact_format}' (ожидали 'pygame')")
        print(f"     skill_id        = '{plan.skill_id}' (ожидали 'interactive_loop')")

        router = EngineerSkillRouter.get_instance()
        used_worker_rules = router.skills.get(plan.skill_id, {}).get("worker_rules", "")
        has_pygame_rules = "pygame.display.set_mode" in used_worker_rules
        print(f"     Инженер получил правила Pygame? -> {has_pygame_rules}")

        if norm_fmt == "cli" and plan.artifact_format == "cli" and plan.skill_id != "interactive_loop":
            print(">>> [REPRODUCED BUG-1] Формат 'interactive_loop' потерян -> 'cli', ручной скилл перезаписан на 'cli_automation'!")
            bugs_reproduced += 1
        else:
            print(">>> [NOT REPRODUCED BUG-1]")

    # --- BUG-2: MissingEntryPoint + слепой SubstanceGuard ---
    print("\n[2A] CodeValidator.validate_python_syntax без __main__ (ход 1):")
    ok_syntax, syntax_msg = CodeValidator.validate_python_syntax("mario_core.py", MARIO_NO_MAIN, is_entry_file=True)
    print(f"     is_valid={ok_syntax} | msg={syntax_msg}")

    with tempfile.TemporaryDirectory() as tmpdir:
        broken_file = Path(tmpdir) / "mario_core.py"
        broken_file.write_text(MARIO_BROKEN_CODE, encoding="utf-8")
        is_solid, subst_msg = ToolEngine.check_artifact_substance(broken_file, min_bytes=350)
        print(f"[2B] ToolEngine.check_artifact_substance(broken mario_core.py):")
        print(f"     is_solid={is_solid} | msg={subst_msg}")
        if (not ok_syntax and "MissingEntryPoint" in (syntax_msg or "")) and is_solid:
            print(">>> [REPRODUCED BUG-2] Ход 1 рубится на MissingEntryPoint, а пустышка с delay(100) и без отрисовки игрока проходит SubstanceGuard!")
            bugs_reproduced += 1
        else:
            print(">>> [NOT REPRODUCED BUG-2]")

    # --- BUG-3: Слепой Smoke-тест GUI в SandboxManager ---
    print("\n[3] SandboxManager.run_script (GUI smoke-тест):")
    orig_get_ws = SandboxManager.get_workspace_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        SandboxManager.get_workspace_dir = classmethod(lambda cls, _p=tmp_ws: _p)
        try:
            f1 = tmp_ws / "repro_mario_broken.py"
            f1.write_text(MARIO_BROKEN_CODE, encoding="utf-8")
            res1 = SandboxManager.run_script("repro_mario_broken.py")
            print(f"     broken mario_core.py -> success={res1.success}, rc={res1.return_code}, out={res1.output.strip()!r}")

            f2 = tmp_ws / "repro_empty_stub.py"
            f2.write_text(EMPTY_PYGAME_STUB, encoding="utf-8")
            res2 = SandboxManager.run_script("repro_empty_stub.py")
            print(f"     empty pygame stub    -> success={res2.success}, rc={res2.return_code}, out={res2.output.strip()!r}")

            if res1.success and res2.success:
                print(">>> [REPRODUCED BUG-3] Оба неиграбельных скрипта (нет отрисовки Марио / пустой чёрный экран) получили success=True!")
                bugs_reproduced += 1
            else:
                print(">>> [NOT REPRODUCED BUG-3]")
        finally:
            SandboxManager.get_workspace_dir = orig_get_ws

    print("\n" + "=" * 80)
    print(f"ИТОГ БАЗОВОГО ПРОГОНА: воспроизведено багов {bugs_reproduced}/3")
    print("=" * 80)


if __name__ == "__main__":
    main()
