import sys, re, json, inspect
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.model_router import ModelRouter
from services.art_director import director as dir_mod
from services.art_director.director import ArtDirector
from services.art_director.plan import GenerationPlan, is_sdxl_checkpoint
from services.art_director.geometry import is_xl_model

def patched_build_direct_plan(self, raw_prompt: str, preferred_style=None, lm_client=None, available_models=None, seed=None) -> GenerationPlan:
    clean_raw = re.sub(r'^(?:!art|!арт|!img|!картинка)\s*', '', raw_prompt, flags=re.IGNORECASE).strip()
    clean_raw = dir_mod.tagify_prompt_with_lm(clean_raw, lm_client=lm_client)
    pos_raw, neg_raw, user_params = dir_mod.parse_direct_command_params(clean_raw)

    chosen_style = user_params.get("model_override") or preferred_style
    selected_profile = None
    router_profile = None
    actual_model_ckpt = None
    best_model_id = None
    has_router = hasattr(self, "router") and self.router is not None

    if chosen_style and not str(chosen_style).startswith("✨") and str(chosen_style).lower() not in ("default", "none", "auto"):
        if has_router:
            router_profile = self.router.get_profile(chosen_style)
            actual_model_ckpt = router_profile.get("checkpoint") if router_profile else self.router.get_checkpoint_for_model(chosen_style)
        if not actual_model_ckpt and str(chosen_style).lower().endswith(".safetensors"):
            actual_model_ckpt = str(chosen_style)
        if not router_profile and actual_model_ckpt and has_router:
            router_profile = self.router.get_profile(actual_model_ckpt)
        chosen_key = str(chosen_style).lower()
        selected_profile = dir_mod.MODEL_REGISTRY[chosen_key] if chosen_key in dir_mod.MODEL_REGISTRY else dir_mod.MODEL_REGISTRY.get(dir_mod.detect_style_from_checkpoint(actual_model_ckpt or str(chosen_style)))

    if not router_profile and not selected_profile:
        if has_router:
            best_model_id, _ = self.router.match_prompt(pos_raw)
            router_profile = self.router.get_profile(best_model_id)
            actual_model_ckpt = router_profile.get("checkpoint") if router_profile else self.router.get_checkpoint_for_model(best_model_id)
        if best_model_id and best_model_id in dir_mod.MODEL_REGISTRY:
            selected_profile = dir_mod.MODEL_REGISTRY[best_model_id]
        else:
            ckpt = actual_model_ckpt or best_model_id or ""
            selected_profile = dir_mod.MODEL_REGISTRY.get(dir_mod.detect_style_from_checkpoint(ckpt)) or dir_mod.MODEL_REGISTRY.get("realistic", list(dir_mod.MODEL_REGISTRY.values())[0])

    if not selected_profile:
        ckpt_hint = actual_model_ckpt or (router_profile.get("checkpoint") if router_profile else "") or ""
        selected_profile = dir_mod.MODEL_REGISTRY.get(dir_mod.detect_style_from_checkpoint(ckpt_hint)) or dir_mod.MODEL_REGISTRY.get("realistic", list(dir_mod.MODEL_REGISTRY.values())[0])

    canon = selected_profile.canon
    final_positive = dir_mod.sanitize_prompt(pos_raw)

    if actual_model_ckpt:
        target_ckpt = actual_model_ckpt
    elif router_profile and router_profile.get("checkpoint"):
        target_ckpt = router_profile["checkpoint"]
    elif chosen_style and str(chosen_style).lower().endswith(".safetensors"):
        target_ckpt = str(chosen_style)
    else:
        target_ckpt = selected_profile.canonical_filename
        if available_models:
            matched = dir_mod.find_checkpoint_for_style(selected_profile.id, available_models)
            if matched:
                target_ckpt = matched

    if not router_profile and has_router and target_ckpt:
        router_profile = self.router.get_profile(target_ckpt)

    model_pipeline = (router_profile.get("pipeline") or {}) if isinstance(router_profile, dict) else {}
    plan_model_id = router_profile.get("id", selected_profile.id) if router_profile else selected_profile.id
    plan_model_title = router_profile.get("name", selected_profile.title) if router_profile else selected_profile.title

    if neg_raw:
        final_negative = dir_mod.sanitize_prompt(neg_raw)
    else:
        custom_neg_from_profile = (model_pipeline.get("negative_prompt") or router_profile.get("negative_prompt") or "").strip() if isinstance(router_profile, dict) else ""
        final_negative = dir_mod.build_negative_prompt(pos_raw, selected_profile.id, canon.base_negative, archetype="solo", custom_base_neg=custom_neg_from_profile)

    if isinstance(router_profile, dict):
        model_target = dict(router_profile)
        if target_ckpt and target_ckpt != model_target.get("checkpoint"):
            model_target["checkpoint"] = target_ckpt
            if is_xl_model(target_ckpt) or is_sdxl_checkpoint(target_ckpt):
                model_target["architecture"] = "sdxl"
            elif model_target.get("architecture") == "sdxl" and not is_xl_model(target_ckpt):
                model_target["architecture"] = "sd15"
    else:
        model_target = f"{target_ckpt} {selected_profile.id} {selected_profile.title}"

    is_xl = is_xl_model(model_target) or is_sdxl_checkpoint(target_ckpt)
    effective_seed = user_params.get("seed", seed if (seed is not None and seed != -1) else -1)
    steps = int(user_params.get("steps", model_pipeline.get("steps", canon.recommended_steps)))
    cfg_scale = float(user_params.get("cfg_scale", model_pipeline.get("cfg_scale", canon.default_cfg)))
    clip_skip = int(user_params.get("clip_skip", model_pipeline.get("clip_skip", canon.clip_skip)))

    raw_sampler = model_pipeline.get("sampler", canon.sampler_name if canon else "DPM++ 2M")
    custom_scheduler = model_pipeline.get("scheduler")
    if isinstance(raw_sampler, str) and raw_sampler.endswith(" Karras"):
        sampler_name = raw_sampler[:-7].strip()
        scheduler = "Karras"
    elif custom_scheduler:
        sampler_name = raw_sampler
        scheduler = custom_scheduler
    elif any(k in str(raw_sampler) for k in ("Euler a", "Euler", "DDIM")):
        sampler_name = raw_sampler
        scheduler = "Automatic"
    else:
        sampler_name = raw_sampler
        scheduler = canon.scheduler if canon else "Karras"

    if "enable_hr" in user_params:
        enable_hr = bool(user_params["enable_hr"])
        hr_scale = float(user_params.get("hr_scale", model_pipeline.get("hr_scale", 1.5))) if enable_hr else 1.0
    elif "enable_hr" in model_pipeline and model_pipeline["enable_hr"] is not None:
        enable_hr = bool(model_pipeline["enable_hr"])
        hr_scale = float(model_pipeline.get("hr_scale", 1.5)) if enable_hr else 1.0
    elif is_xl:
        enable_hr, hr_scale = False, 1.0
    else:
        enable_hr = True
        hr_scale = float(user_params.get("hr_scale", model_pipeline.get("hr_scale", 1.5)))

    hr_denoise = float(user_params.get("denoising_strength", model_pipeline.get("hires_denoise", 0.35)))
    hr_upscaler = user_params.get("hr_upscaler", model_pipeline.get("hires_upscaler", canon.hr_upscaler or "R-ESRGAN 4x+"))
    hr_second_pass_steps = int(user_params.get("hr_second_pass_steps", model_pipeline.get("hr_second_pass_steps", canon.hr_second_pass_steps or 14)))

    ad_cfg = self._resolve_adetailer_pipeline(model_pipeline=model_pipeline, canon=canon, is_bg=False, is_xl=is_xl, wants_hands=False)
    if "enable_adetailer" in user_params:
        enable_ad = bool(user_params["enable_adetailer"])
        hands_enabled = bool(ad_cfg["hands_enabled"]) if enable_ad else False
    else:
        enable_ad = bool(ad_cfg["enable_adetailer"])
        hands_enabled = bool(ad_cfg["hands_enabled"])

    default_w, default_h = (1024, 1024) if is_xl else (512, 768)
    width = int(user_params.get("width", default_w))
    height = int(user_params.get("height", default_h))

    return GenerationPlan(
        style=plan_model_id, target_model_key=plan_model_id, model_title=plan_model_title, target_checkpoint=target_ckpt,
        positive_prompt=final_positive, negative_prompt=final_negative, width=width, height=height,
        steps=steps, cfg_scale=cfg_scale, sampler_name=sampler_name, scheduler=scheduler,
        enable_hr=enable_hr, hr_scale=hr_scale, hr_upscaler=hr_upscaler, denoising_strength=hr_denoise,
        hr_second_pass_steps=hr_second_pass_steps, clip_skip=clip_skip,
        enable_adetailer=enable_ad, enable_hand_adetailer=hands_enabled,
        ad_denoising_strength=ad_cfg["face_denoise"] if enable_ad else None,
        ad_prompt=final_positive if enable_ad else None, ad_negative_prompt=final_negative if enable_ad else None,
        ad_mask_blur=4, seed=effective_seed, hires_upscaler=hr_upscaler, hires_denoise=hr_denoise,
        reasoning="Прямой ручной инференс (!арт)", style_badge="⚡ Директ-арт",
        adetailer_face_model=ad_cfg["face_model"], adetailer_face_confidence=ad_cfg["face_confidence"],
        adetailer_face_denoise=ad_cfg["face_denoise"], adetailer_face_inpaint_size=ad_cfg["face_inpaint_size"],
        adetailer_inpaint_size=ad_cfg["face_inpaint_size"], adetailer_hands_enabled=hands_enabled,
        adetailer_hands_model=ad_cfg["hands_model"], adetailer_hands_confidence=ad_cfg["hands_confidence"],
        adetailer_hands_denoise=ad_cfg["hands_denoise"], adetailer_hands_inpaint_size=ad_cfg["hands_inpaint_size"],
        adetailer_hands_mask_blur=ad_cfg["hands_mask_blur"], adetailer_hands=hands_enabled,
        adetailer_hands_prompt=final_positive if hands_enabled else None,
        adetailer_hands_negative_prompt=final_negative if hands_enabled else None,
    )

router = ModelRouter()
sig = inspect.signature(ArtDirector.__init__)
kwargs = {"router": router} if "router" in sig.parameters else ({"model_router": router} if "model_router" in sig.parameters else {})
director = ArtDirector(**kwargs)
if not getattr(director, "router", None):
    director.router = router
director.build_direct_plan = patched_build_direct_plan.__get__(director, ArtDirector)

models_data = json.loads((ROOT / "data" / "art_director_models.json").read_text(encoding="utf-8-sig"))["models"]
available_models = [{"model_name": m["checkpoint"], "title": m["checkpoint"]} for m in models_data]

for ckpt_arg in ("Juggernaut_XI_Light_byRunDiffusion.safetensors", "Juggernaut_XI_Light_byRunDiffusion", "auto_juggernaut_xi_light_byrundiffusion"):
    p = director.build_direct_plan("!арт Midshot, woman, dusk setting", preferred_style=ckpt_arg, available_models=available_models)
    assert p.target_checkpoint == "Juggernaut_XI_Light_byRunDiffusion.safetensors"
    assert p.model_title == "Juggernaut_XI_Light_byRunDiffusion"
    assert (p.width, p.height) == (1024, 1024)
    assert p.steps == 6 and p.cfg_scale == 2.0 and p.clip_skip == 2
    assert p.sampler_name == "DPM++ 2M" and p.scheduler == "Karras"
    assert p.enable_hr is False and p.hr_upscaler == "4x_NMKD-Siax_200k" and p.denoising_strength == 0.5
    assert p.enable_adetailer is False

p_cli = director.build_direct_plan("!арт red car --steps 12 --cfg 3.5 --w 832 --h 1216 --hr on --scale 1.5 --ad on", preferred_style="Juggernaut_XI_Light_byRunDiffusion.safetensors", available_models=available_models)
assert (p_cli.width, p_cli.height) == (832, 1216) and p_cli.steps == 12 and p_cli.cfg_scale == 3.5
assert p_cli.enable_hr is True and p_cli.hr_scale == 1.5 and p_cli.enable_adetailer is True

print("=== ALL 10 PROFILES CHECK ===")
for m in models_data:
    pm = director.build_direct_plan("!арт test scene", preferred_style=m["checkpoint"], available_models=available_models)
    exp_pipe = m["pipeline"]
    assert pm.target_checkpoint == m["checkpoint"], (m["name"], pm.target_checkpoint)
    assert pm.steps == int(exp_pipe["steps"]), (m["name"], pm.steps, exp_pipe["steps"])
    assert pm.cfg_scale == float(exp_pipe["cfg_scale"]), (m["name"], pm.cfg_scale, exp_pipe["cfg_scale"])
    assert pm.enable_hr == bool(exp_pipe["enable_hr"]), (m["name"], pm.enable_hr, exp_pipe["enable_hr"])
    assert pm.enable_adetailer == bool(exp_pipe["enable_adetailer"]), (m["name"], pm.enable_adetailer, exp_pipe["enable_adetailer"])
    print(f"  OK: {m['name']} -> {pm.width}x{pm.height}, steps={pm.steps}, cfg={pm.cfg_scale}, sampler={pm.sampler_name}/{pm.scheduler}, hr={pm.enable_hr}, ad={pm.enable_adetailer}")

print("\nALL STAND ASSERTIONS PASSED!")
