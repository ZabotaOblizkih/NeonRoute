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

GOOD_PYGAME_MARIO = """import pygame

class Player:
    def __init__(self, x=100, y=100):
        self.x = x
        self.y = y
        self.vx = 0
        self.vy = 0
        self.width = 30
        self.height = 40
        self.on_ground = False

    def update(self, keys, level):
        self.vx = 0
        if keys[pygame.K_LEFT]:
            self.vx = -4
        if keys[pygame.K_RIGHT]:
            self.vx = 4
        if keys[pygame.K_SPACE] and self.on_ground:
            self.vy = -10
            self.on_ground = False

        self.vy += 0.5  # гравитация
        self.x += self.vx
        self.y += self.vy

        rect = pygame.Rect(int(self.x), int(self.y), self.width, self.height)
        for platform in level.platforms:
            if rect.colliderect(platform):
                if self.vy > 0:
                    self.y = platform.top - self.height
                    self.vy = 0
                    self.on_ground = True

    def draw(self, screen):
        pygame.draw.rect(screen, (255, 0, 0), (int(self.x), int(self.y), self.width, self.height))


class Level:
    def __init__(self):
        self.platforms = [
            pygame.Rect(0, 500, 800, 100),
            pygame.Rect(200, 400, 200, 20),
            pygame.Rect(500, 300, 200, 20)
        ]

    def draw(self, screen):
        for p in self.platforms:
            pygame.draw.rect(screen, (0, 200, 0), p)


def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    clock = pygame.time.Clock()
    player = Player(100, 100)
    level = Level()
    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        keys = pygame.key.get_pressed()
        player.update(keys, level)

        screen.fill((50, 50, 80))
        level.draw(screen)
        player.draw(screen)
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == '__main__':
    main()
"""

STUB_METHOD_CODE = """def calculate_total(items):
    \"\"\"Docstring without code\"\"\"
    pass

class OrderProcessor:
    def process(self):
        ...
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
    print("VERIFICATION STAND: NeonRoute Engineer Pipeline (Games & GUI)")
    print("=" * 80)

    all_passed = True

    # --- 1A: Проверка маппинга формата ---
    norm_fmt = EngineerContext.normalize_artifact_format(
        "interactive_loop", "Сделай простую игру марио", has_verification_script=False
    )
    print(f"\n[1A] normalize_artifact_format('interactive_loop', 'Сделай простую игру марио') -> '{norm_fmt}'")
    if norm_fmt == "pygame":
        print("  -> PASSED: формат корректно нормализован в 'pygame'")
    else:
        print(f"  -> FAILED: ожидали 'pygame', получили '{norm_fmt}'")
        all_passed = False

    # --- 1B: Защита принудительного скилла и инъекция правил worker_rules ---
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        plan = EngineerPlanner.plan_architecture(
            task_prompt="Сделай простую игру марио",
            ws_dir=tmp_ws,
            client=FakeArchitectClient(),
            target_model="fake-coder",
            active_engineer_skill="interactive_loop",
        )
        print(f"\n[1B] ArchitectPlan при active_engineer_skill='interactive_loop':")
        print(f"     artifact_format = '{plan.artifact_format}' (ожидали 'pygame')")
        print(f"     skill_id        = '{plan.skill_id}' (ожидали 'interactive_loop')")

        router = EngineerSkillRouter.get_instance()
        used_worker_rules = router.skills.get(plan.skill_id, {}).get("worker_rules", "")
        has_pygame_rules = "pygame.display.set_mode" in plan.system_prompt
        print(f"     Инженер получил правила Pygame в system_prompt? -> {has_pygame_rules}")

        if plan.artifact_format == "pygame" and plan.skill_id == "interactive_loop" and has_pygame_rules:
            print("  -> PASSED: скилл зафиксирован, формат 'pygame', правила инжектированы")
        else:
            print("  -> FAILED: скилл или формат потеряны или правила не попали в system_prompt")
            all_passed = False

    # --- 2A: Проверка MissingEntryPoint ---
    print("\n[2A] CodeValidator.validate_python_syntax без __main__:")
    ok_syntax, syntax_msg = CodeValidator.validate_python_syntax("mario_core.py", MARIO_NO_MAIN, is_entry_file=True)
    print(f"     is_valid={ok_syntax} | msg={syntax_msg}")
    if not ok_syntax and "MissingEntryPoint" in (syntax_msg or ""):
        print("  -> PASSED: MissingEntryPoint корректно обнаружен")
    else:
        print("  -> FAILED: код без точки входа не был отклонен")
        all_passed = False

    # --- 2B: Статический гейт CodeValidator (запрет pygame.time.delay) и ToolEngine.check_artifact_substance ---
    print("\n[2B] Статическая валидация сломанного кода и методов-заглушек:")
    ok_delay, delay_msg = CodeValidator.validate_python_syntax("mario_core.py", MARIO_BROKEN_CODE, is_entry_file=True)
    print(f"     MARIO_BROKEN_CODE (delay 100) -> is_valid={ok_delay} | msg={delay_msg}")
    if not ok_delay and "delay" in (delay_msg or "").lower():
        print("  -> PASSED: pygame.time.delay успешно заблокирован CodeValidator")
    else:
        print("  -> FAILED: блокирующий delay не был пойман CodeValidator")
        all_passed = False

    with tempfile.TemporaryDirectory() as tmpdir:
        stub_file = Path(tmpdir) / "stub_sample.py"
        stub_file.write_text(STUB_METHOD_CODE, encoding="utf-8")
        is_solid_stub, subst_msg_stub = ToolEngine.check_artifact_substance(stub_file, min_bytes=50)
        print(f"     STUB_METHOD_CODE -> is_solid={is_solid_stub} | msg={subst_msg_stub}")
        if not is_solid_stub:
            print("  -> PASSED: методы-заглушки (pass / ...) успешно отклонены ToolEngine.check_artifact_substance")
        else:
            print("  -> FAILED: методы-заглушки не были отклонены SubstanceGuard")
            all_passed = False

    # --- 3: Рантайм контроль в SandboxManager ---
    print("\n[3] SandboxManager.run_script (негативные рантайм-сценарии):")
    orig_get_ws = SandboxManager.get_workspace_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        SandboxManager.get_workspace_dir = classmethod(lambda cls, _p=tmp_ws: _p)
        try:
            f1 = tmp_ws / "repro_mario_broken.py"
            f1.write_text(MARIO_BROKEN_CODE, encoding="utf-8")
            res1 = SandboxManager.run_script("repro_mario_broken.py")
            print(f"     broken mario_core.py -> success={res1.success}, rc={res1.return_code}")
            print(f"     out={res1.output.strip()!r}")
            if not res1.success:
                print("  -> PASSED: broken mario_core.py отклонен в песочнице")
            else:
                print("  -> FAILED: broken mario_core.py ложно получил success=True")
                all_passed = False

            f2 = tmp_ws / "repro_empty_stub.py"
            f2.write_text(EMPTY_PYGAME_STUB, encoding="utf-8")
            res2 = SandboxManager.run_script("repro_empty_stub.py")
            print(f"     empty pygame stub    -> success={res2.success}, rc={res2.return_code}")
            print(f"     out={res2.output.strip()!r}")
            if not res2.success:
                print("  -> PASSED: empty pygame stub отклонен в песочнице")
            else:
                print("  -> FAILED: empty pygame stub ложно получил success=True")
                all_passed = False

            # --- 4: Позитивный тест GOOD_PYGAME_MARIO ---
            print("\n[4] Позитивный сценарий (GOOD_PYGAME_MARIO):")
            ok_good_syntax, good_syntax_msg = CodeValidator.validate_python_syntax(
                "game.py", GOOD_PYGAME_MARIO, is_entry_file=True
            )
            print(f"     CodeValidator: is_valid={ok_good_syntax} | msg={good_syntax_msg}")
            if not ok_good_syntax:
                print("  -> FAILED: валидный GOOD_PYGAME_MARIO отклонен синтаксическим гейтом")
                all_passed = False

            f_good = tmp_ws / "good_mario.py"
            f_good.write_text(GOOD_PYGAME_MARIO, encoding="utf-8")
            is_solid_good, subst_msg_good = ToolEngine.check_artifact_substance(f_good, min_bytes=300)
            print(f"     ToolEngine: is_solid={is_solid_good} | msg={subst_msg_good}")
            if not is_solid_good:
                print("  -> FAILED: валидный GOOD_PYGAME_MARIO отклонен SubstanceGuard")
                all_passed = False

            res_good = SandboxManager.run_script("good_mario.py")
            print(f"     SandboxManager: success={res_good.success}, rc={res_good.return_code}")
            print(f"     out={res_good.output.strip()!r}")
            if res_good.success and res_good.return_code == 0:
                print("  -> PASSED: полноценная игра успешно прошла рендер, ввод и проверку SandboxManager!")
            else:
                print("  -> FAILED: полноценная игра не прошла рантайм-валидацию")
                all_passed = False

        finally:
            SandboxManager.get_workspace_dir = orig_get_ws

    print("\n" + "=" * 80)
    print(f"ИТОГ ВЕРИФИКАЦИИ СТЕНДА: {'ВСЕ ТЕСТЫ ПРОЙДЕНЫ (SUCCESS)' if all_passed else 'ЕСТЬ ОШИБКИ (FAIL)'}")
    print("=" * 80)
    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
