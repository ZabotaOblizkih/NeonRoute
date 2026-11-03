import sys, inspect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

targets = [
    ("services.hybrid_search", "HybridSearchEngine"),
    ("services.roleplay_state_manager", "RoleplayStateManager"),
    ("services.tavern_parser", "TavernParser"),
    ("services.token_budget_manager", "TokenBudgetManager"),
    ("services.mode_coordinator", "ModeCoordinator"),
]

for mod_name, cls_name in targets:
    mod = __import__(mod_name, fromlist=[cls_name])
    cls = getattr(mod, cls_name)
    print(f"=== {mod_name}.{cls_name} ===")
    for name, obj in inspect.getmembers(cls, predicate=lambda x: inspect.isfunction(x) or inspect.ismethod(x)):
        if not name.startswith("_") or name == "__init__":
            print(f"  def {name}{inspect.signature(obj)}")
