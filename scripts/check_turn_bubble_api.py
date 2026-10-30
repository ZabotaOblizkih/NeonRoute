import sys
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

eng_path = Path("ui/controllers/engineer_controller.py")
if eng_path.exists():
    lines = eng_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, l in enumerate(lines):
        if "_turn_bubble." in l:
            print(f"Строка {idx+1}: {l.strip()}")
