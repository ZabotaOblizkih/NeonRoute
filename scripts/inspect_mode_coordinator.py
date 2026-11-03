import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

from services.mode_coordinator import ModeCoordinator

methods = [m for m in dir(ModeCoordinator) if not m.startswith("_") and callable(getattr(ModeCoordinator, m))]
print(f"[*] Публичные методы ModeCoordinator: {methods}")
