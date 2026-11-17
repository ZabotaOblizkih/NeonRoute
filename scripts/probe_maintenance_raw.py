import re
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.engineer_prompts import get_engineer_system_prompt
from services.engineer_skill_router import ENGINEER_SKILLS
from services.engineer_parser import EngineerResponseParser
from services.lm_studio import LMStudioClient

MODEL_ID = "Qwen2.5-Coder-7B-Instruct-GGUF/qwen2.5-coder-7b-instruct-q5_k_m.gguf"

SAMPLE_GAME_94 = """import pygame, sys
pygame.init()
SCREEN_WIDTH, SCREEN_HEIGHT, FPS = 800, 600, 60
WHITE, GREEN, BLUE = (255, 255, 255), (0, 255, 0), (0, 0, 255)
GRAVITY = 0.5
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
clock = pygame.time.Clock()

class Player:
    def __init__(self, x, y):
        self.x, self.y, self.width, self.height = x, y, 50, 50
        self.velocity_y, self.on_ground = 0, False
    def update(self):
        self.velocity_y += GRAVITY
        self.y += self.velocity_y
        if self.y > SCREEN_HEIGHT - self.height:
            self.y = SCREEN_HEIGHT - self.height
            self.velocity_y = 0
            self.on_ground = True
    def draw(self, screen):
        pygame.draw.rect(screen, BLUE, (self.x, self.y, self.width, self.height))

class Level:
    def __init__(self):
        self.platforms = [pygame.Rect(0, SCREEN_HEIGHT - 50, SCREEN_WIDTH, 50), pygame.Rect(200, SCREEN_HEIGHT - 150, 200, 50)]
    def draw(self, screen):
        for p in self.platforms:
            pygame.draw.rect(screen, GREEN, p)
    def colliderect(self, rect):
        return any(rect.colliderect(p) for p in self.platforms)

def main():
    player, level = Player(100, 100), Level()
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT]: player.x -= 5
        if keys[pygame.K_RIGHT]: player.x += 5
        if keys[pygame.K_SPACE] and player.on_ground:
            player.velocity_y = -10; player.on_ground = False
        player.update()
        screen.fill(WHITE)
        level.draw(screen)
        player.draw(screen)
        pygame.display.flip()
        clock.tick(FPS)

if __name__ == '__main__':
    main()
"""

def run_probe():
    client = LMStudioClient()
    print(f"Загружаю {MODEL_ID}...")
    client.ensure_model_loaded(preferred_model=MODEL_ID, context_length=28672, gpu_ratio="1.0")

    sys_p = get_engineer_system_prompt(
        artifact_format="pygame",
        injected_worker_rules=ENGINEER_SKILLS["interactive_loop"]["worker_rules"],
    )
    ticks = chr(96) * 3
    user_p = (
        "Добавь в игру монеты и врагов с обработкой коллизий.\n\n"
        "### УТВЕРЖДЁННЫЙ АРХИТЕКТУРНЫЙ ПЛАН (Формат: pygame, Intent: maintenance, Целевой артефакт: game.py):\n"
        "Добавление монет и врагов в игру Mario с обработкой коллизий.\n\n"
        "Текущий код файлов для доработки:\n\n"
        "[ТЕКУЩАЯ ВЕРСИЯ ФАЙЛА НА ДИСКЕ: game.py (94 строк)]\n"
        f"{ticks}python\n{SAMPLE_GAME_94}\n{ticks}\n"
        "Внимание: строго сохраняй существующие имена атрибутов, классов и логику при модификации.\n\n"
        "План утверждён. Приступай к двухфазной доработке файла game.py (94 строк <= 250 строк):\n"
        "1. ФАЗА 1: Краткий текстовый диагноз (в какой функции баг / что конкретно меняется).\n"
        '2. ФАЗА 2: Полная сборка монолитного файла через <<TOOL:write_file filename="game.py">> '
        "(без заглушек и без номеров строк, внутри <<TOOL:write_file>> должен быть весь готовый код, а не сокращения и не отдельный блок Markdown).\n"
        '3. запусти главную точку входа через <<TOOL:run_script filename="game.py">>.'
    )

    print("Отправляю запрос к Qwen2.5-Coder-7B (temp=0.2)...")
    resp = client.chat_completion(
        messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
        model=MODEL_ID,
        temperature=0.2,
        max_tokens=4096,
        stream=False,
        emit_signal=False,
    )
    raw = str(resp or "")
    print(f"\n=== СЫРОЙ ОТВЕТ МОДЕЛИ (len={len(raw)}) ===")
    print(raw)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        (tmp_ws / "game.py").write_text(SAMPLE_GAME_94, encoding="utf-8")
        actions = EngineerResponseParser.parse_turn_actions(
            response_text=raw,
            ws_dir=tmp_ws,
            plan=None,
            task_prompt="Добавь монеты и врагов",
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
            file_backups={"game.py": SAMPLE_GAME_94},
        )
        print("\n=== ИТОГ parse_turn_actions ===")
        for fn, cnt in actions.write_blocks:
            print(f"Файл: {fn} -> len={len(cnt)}")
            print(repr(cnt[:500]))

run_probe()
