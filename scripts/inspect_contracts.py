import sys
import inspect
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

targets = [
    ("services.vram_arbiter", "VRAMArbiter"),
    ("services.layer_budgeter", "LayerBudgeter"),
    ("services.thought_stream_parser", "ThoughtStreamParser"),
    ("services.code_validator", "CodeValidator"),
    ("ui.common.ui_context", "UIContext"),
    ("services.sandbox_manager", "SandboxManager"),
    ("services.session_manager", "SessionManager"),
]

for mod_name, cls_name in targets:
    try:
        mod = __import__(mod_name, fromlist=[cls_name])
        cls = getattr(mod, cls_name)
        methods = [
            f"{name}{inspect.signature(obj)}"
            for name, obj in inspect.getmembers(cls, predicate=lambda x: inspect.isfunction(x) or inspect.ismethod(x))
            if not name.startswith("_") or name == "__init__"
        ]
        props = [
            name for name, obj in inspect.getmembers(cls, predicate=lambda x: isinstance(x, property))
        ]
        print(f"=== {mod_name}.{cls_name} ===")
        if props:
            print("  Properties:", ", ".join(props))
        for m in methods:
            print("  def", m)
    except Exception as e:
        print(f"=== {mod_name}.{cls_name} ERROR: {e} ===")
