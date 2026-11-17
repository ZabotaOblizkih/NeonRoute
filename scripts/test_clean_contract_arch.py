import sys
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tool_engine import ToolEngine

def test_clean_architecture():
    # Сценарий 1: ООП-программа (игра, GUI, сервис). Классы из spec есть, а свободные функции из spec инкапсулированы в методы классов.
    oop_code = """import sys

class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
    def update(self):
        self.x += 5
    def draw(self, screen):
        return (self.x, self.y)

class Level:
    def __init__(self):
        self.platforms = [1, 2]
    def draw(self, screen):
        return len(self.platforms)

def main():
    p = Player(0, 0)
    lvl = Level()
    p.update()
    lvl.draw(None)

if __name__ == "__main__":
    main()
"""
    spec_with_procedural_noise = {
        "classes": [
            {"name": "Player", "methods": [{"name": "__init__"}, {"name": "update"}, {"name": "draw"}]},
            {"name": "Level", "methods": [{"name": "__init__"}, {"name": "draw"}]}
        ],
        "functions": [
            {"name": "main"},
            {"name": "player_move_left"},
            {"name": "some_arbitrary_helper_invented_by_architect"}
        ]
    }

    # Сценарий 2: В spec заявлен новый предметный класс (например, Enemy или DatabasePool), но Кодер его забыл написать!
    spec_with_missing_class = {
        "classes": [
            {"name": "Player", "methods": [{"name": "update"}]},
            {"name": "Enemy", "methods": [{"name": "update"}, {"name": "draw"}]}
        ],
        "functions": []
    }

    tmp = ROOT / "scripts" / "_tmp_clean_arch.py"
    try:
        tmp.write_text(oop_code + "\n" + "# pad\n" * 20, encoding="utf-8")
        ok1, err1 = ToolEngine.check_artifact_substance(tmp, min_bytes=100, interface_spec=spec_with_procedural_noise)
        print(f"1. ООП-код с произвольными функциями-фантазиями в spec (ожидается True): ok={ok1}, err={err1!r}")

        ok2, err2 = ToolEngine.check_artifact_substance(tmp, min_bytes=100, interface_spec=spec_with_missing_class)
        print(f"2. Отсутствует заявленный класс Enemy (ожидается False): ok={ok2}, err={err2!r}")
    finally:
        if tmp.exists():
            tmp.unlink()

if __name__ == "__main__":
    test_clean_architecture()
