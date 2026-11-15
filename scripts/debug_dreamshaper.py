import sys, json, inspect
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.model_router import ModelRouter, DEFAULT_DATA_PATH
from services.art_director.director import ArtDirector
from services.forge_models import MODEL_REGISTRY

print("DEFAULT_DATA_PATH:", DEFAULT_DATA_PATH)
router = ModelRouter()
print("Router profiles count:", len(router.profiles))
prof = router.get_profile("dreamshaper_8.safetensors")
print("get_profile('dreamshaper_8.safetensors'):", json.dumps(prof, indent=2, ensure_ascii=False) if prof else None)

sig = inspect.signature(ArtDirector.__init__)
kwargs = {"router": router} if "router" in sig.parameters else ({"model_router": router} if "model_router" in sig.parameters else {})
director = ArtDirector(**kwargs)
if not getattr(director, "router", None):
    director.router = router

pipe = prof.get("pipeline", {}) if prof else {}
canon = MODEL_REGISTRY.get("artistic", list(MODEL_REGISTRY.values())[0]).canon
print("_resolve_adetailer_pipeline:", director._resolve_adetailer_pipeline(pipe, canon, is_bg=False, is_xl=False, wants_hands=False))
