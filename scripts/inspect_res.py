import sys, inspect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.sandbox_manager import ScriptExecutionResult
print(inspect.getsource(ScriptExecutionResult))
