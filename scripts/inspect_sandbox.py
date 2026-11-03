import sys
import inspect
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.sandbox_manager import SandboxManager
print(inspect.getsource(SandboxManager.write_file))
