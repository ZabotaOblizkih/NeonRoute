import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inspect
from services.tool_parser import ToolCall

print(f"[*] Сигнатура ToolCall.__init__: {inspect.signature(ToolCall.__init__)}")
print(f"[*] Поля ToolCall: {[f for f in dir(ToolCall) if not f.startswith('_')]}")
