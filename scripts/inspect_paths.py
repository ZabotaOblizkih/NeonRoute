import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config

# Посмотрим все Path/dir константы в config.py
path_vars = {k: getattr(config, k) for k in dir(config) if not k.startswith("__") and ("DIR" in k or "PATH" in k or "FILE" in k)}
print("=== Константы путей в config.py ===")
for k, v in sorted(path_vars.items()):
    print(f"  {k} = {v}")
