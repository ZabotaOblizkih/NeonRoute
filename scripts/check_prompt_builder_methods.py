import inspect
from pathlib import Path
import sys

sys.path.insert(0, str(Path(".").resolve()))
from services.assistant_prompt_builder import AssistantPromptBuilder

print("=" * 70)
print("МЕТОДЫ И АТРИБУТЫ AssistantPromptBuilder")
print("=" * 70)
builder = AssistantPromptBuilder
methods = [m for m in dir(builder) if not m.startswith("__")]
for m in methods:
    val = getattr(builder, m)
    if callable(val):
        print(f"  def {m}{inspect.signature(val)}")
    else:
        print(f"  {m} = {type(val)}")
print("=" * 70)
