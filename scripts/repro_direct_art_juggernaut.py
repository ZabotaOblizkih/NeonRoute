import sys
import json
import inspect
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.model_router import ModelRouter
from services.art_director.director import ArtDirector

router = ModelRouter()
sig = inspect.signature(ArtDirector.__init__)
kwargs = {}
if "router" in sig.parameters:
    kwargs["router"] = router
elif "model_router" in sig.parameters:
    kwargs["model_router"] = router
director = ArtDirector(**kwargs)
if not getattr(director, "router", None):
    director.router = router

models_data = json.loads((ROOT / "data" / "art_director_models.json").read_text(encoding="utf-8-sig"))["models"]
available_models = [{"model_name": m["checkpoint"], "title": m["checkpoint"]} for m in models_data]

cases = [
    ("Juggernaut (ckpt filename)", "!арт Midshot, woman, dusk setting, outdoor", "Juggernaut_XI_Light_byRunDiffusion.safetensors"),
    ("Juggernaut (profile name)", "!арт Midshot, woman, dusk setting, outdoor", "Juggernaut_XI_Light_byRunDiffusion"),
    ("Juggernaut + CLI flags", "!арт red car --steps 12 --cfg 3.5 --w 832 --h 1216", "Juggernaut_XI_Light_byRunDiffusion.safetensors"),
    ("CyberRealistic (SD1.5 AD+HR)", "!арт portrait woman", "cyberrealistic_classicV50Final_pruned_fp16.safetensors"),
]

print("=== REPRO BEFORE PATCH ===")
for label, prompt, ckpt in cases:
    plan = director.build_direct_plan(
        raw_prompt=prompt,
        preferred_style=ckpt,
        lm_client=None,
        available_models=available_models,
    )
    print(f"\n[{label}]")
    print(f"  style={plan.style} | title={plan.model_title} | ckpt={plan.target_checkpoint}")
    print(f"  size={plan.width}x{plan.height} | steps={plan.steps} | cfg={plan.cfg_scale} | clip_skip={plan.clip_skip}")
    print(f"  sampler={plan.sampler_name} | scheduler={plan.scheduler}")
    print(f"  hr={plan.enable_hr} (scale={plan.hr_scale}, upscaler={plan.hr_upscaler}, denoise={plan.denoising_strength})")
    print(f"  ad_face={plan.enable_adetailer} | ad_hands={plan.enable_hand_adetailer}")
    print(f"  neg={plan.negative_prompt[:90]}...")
