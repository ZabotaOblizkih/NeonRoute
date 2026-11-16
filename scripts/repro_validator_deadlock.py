import logging
import sys
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.code_validator import CodeValidator
from services.engineer_skill_router import ENGINEER_SKILLS

CODE_MISSING_METHOD = """import pygame

class Level:
    def draw(self, screen):
        pygame.draw.rect(screen, (255, 255, 255), (0, 500, 800, 100))

def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    level = Level()
    while True:
        level.update()
        pygame.draw.rect(screen, (0, 255, 0), (100, 400, 40, 40))
        pygame.display.flip()

if __name__ == '__main__':
    main()
"""

CODE_STUB_PASS = """import pygame

class Level:
    def draw(self, screen):
        pygame.draw.rect(screen, (255, 255, 255), (0, 500, 800, 100))

    def update(self):
        pass

def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    level = Level()
    while True:
        level.update()
        pygame.draw.rect(screen, (0, 255, 0), (100, 400, 40, 40))
        pygame.display.flip()

if __name__ == '__main__':
    main()
"""


def main():
    print("=" * 80)
    print("T3-5 BASELINE STAND: Validator Deadlock & Misleading Escalation")
    print("=" * 80)
    bugs = 0

    # --- BUG-T5-1: MissingMethod не предлагает удалить лишний вызов ---
    mm_err = CodeValidator.check_cross_module_contracts([("game.py", CODE_MISSING_METHOD)])
    has_remove_alt = bool(mm_err and ("удали" in mm_err.lower() or "лишний вызов" in mm_err.lower()))
    print(f"\n[T5-1] MissingMethod msg: {mm_err!r}")
    if mm_err and not has_remove_alt:
        print(">>> [REPRODUCED BUG-T5-1] MissingMethod жёстко требует добавить def update(), не предлагая удалить лишний вызов!")
        bugs += 1
    else:
        print(">>> [PASS T5-1] MissingMethod даёт оба варианта (реализовать или удалить лишний вызов).")

    # --- BUG-T5-2: AST Stub Guard не пишет имя класса и не предлагает удалить пустой метод ---
    ok_st, st_err = CodeValidator.validate_python_syntax("game.py", CODE_STUB_PASS, is_entry_file=True)
    has_cls_name = bool(st_err and "Level.update" in st_err)
    has_del_alt = bool(st_err and "удали" in st_err.lower())
    print(f"\n[T5-2] AST Stub Guard: valid={ok_st} | msg={st_err!r}")
    if (not ok_st) and (not has_cls_name or not has_del_alt):
        print(">>> [REPRODUCED BUG-T5-2] Stub Guard скрывает имя класса (Level.update) и не подсказывает удалить ненужный пустой метод!")
        bugs += 1
    else:
        print(">>> [PASS T5-2] Stub Guard указывает Class.method и подсказывает вариант удаления.")

    # --- BUG-T5-3: Чужая эскалация про коллекции в subagent_engineer.py + отсутствие main в architect_rules ---
    se_src = (ROOT / "services" / "subagent_engineer.py").read_text(encoding="utf-8", errors="replace")
    # Проверяем, что фраза про инициализацию коллекций не привязана к NameError/UnboundLocalError в escalation_warning
    has_unconditional_nameerror_hint = (
        'исправь инициализацию всех используемых коллекций и переменных' in se_src
        and 'if "NameError" in' not in se_src[max(0, se_src.find('escalation_warning') - 300):se_src.find('escalation_warning') + 600]
    )
    arch_rules = ENGINEER_SKILLS["interactive_loop"]["architect_rules"]
    has_main_rule = "main" in arch_rules and "functions" in arch_rules
    print(f"\n[T5-3] unconditional_nameerror_escalation={has_unconditional_nameerror_hint}, architect_rules_has_main={has_main_rule}")
    if has_unconditional_nameerror_hint or not has_main_rule:
        print(">>> [REPRODUCED BUG-T5-3] Эскалация подсовывает совет от NameError всем ошибкам, а architect_rules не требует main() в functions!")
        bugs += 1
    else:
        print(">>> [PASS T5-3] Эскалация контекстная, architect_rules требует функцию входа main().")

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА ТЗ №5: воспроизведено багов {bugs}/3")
    print("=" * 80)


if __name__ == "__main__":
    main()
