import json
import inspect
from pathlib import Path
import sys

ROOT = Path(r"C:\NeonRoute")
DIRECTOR_PATH = ROOT / "services" / "art_director" / "director.py"

raw_bytes = DIRECTOR_PATH.read_bytes()
has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
enc = "utf-8-sig" if has_bom else "utf-8"
text = raw_bytes.decode(enc)
newline = "\r\n" if "\r\n" in text else "\n"
text_lf = text.replace("\r\n", "\n")

start_marker = "    def build_direct_plan(\n"
end_marker = "\n    def build_scene_background_plan(\n"

assert text_lf.count(start_marker) == 1, "start_marker not unique!"
assert text_lf.count(end_marker) == 1, "end_marker not unique!"

start_idx = text_lf.index(start_marker)
end_idx = text_lf.index(end_marker, start_idx)

new_method = '''    def build_direct_plan(
        self,
        raw_prompt: str,
        preferred_style: Optional[str] = None,
        lm_client: Optional[Any] = None,
        available_models: Optional[List[Dict[str, Any]]] = None,
        seed: Optional[int] = None
    ) -> GenerationPlan:
        """
        Изолированный прямой инференс (!арт / !art).
        Полностью минует LLM и авто-инжекты, берет параметры pipeline и архитектуру
        из профиля модели в art_director_models.json с приоритетом ручных CLI-флагов.
        """
        clean_raw = re.sub(r'^(?:!art|!арт|!img|!картинка)\\s*', '', raw_prompt, flags=re.IGNORECASE).strip()
        clean_raw = tagify_prompt_with_lm(clean_raw, lm_client=lm_client)
        pos_raw, neg_raw, user_params = parse_direct_command_params(clean_raw)

        # Надежное разрешение профиля модели из ModelRouter + fallback на MODEL_REGISTRY
        chosen_style = user_params.get("model_override") or preferred_style
        selected_profile: Optional[ModelProfile] = None
        router_profile: Optional[Dict[str, Any]] = None
        actual_model_ckpt: Optional[str] = None
        best_model_id: Optional[str] = None
        has_router = hasattr(self, "router") and self.router is not None

        if chosen_style and not str(chosen_style).startswith("✨") and str(chosen_style).lower() not in ("default", "none", "auto"):
            if has_router:
                router_profile = self.router.get_profile(chosen_style)
                actual_model_ckpt = (
                    router_profile.get("checkpoint")
                    if router_profile
                    else self.router.get_checkpoint_for_model(chosen_style)
                )
            if not actual_model_ckpt and str(chosen_style).lower().endswith(".safetensors"):
                actual_model_ckpt = str(chosen_style)
            if not router_profile and actual_model_ckpt and has_router:
                router_profile = self.router.get_profile(actual_model_ckpt)

            chosen_key = str(chosen_style).lower()
            if chosen_key in MODEL_REGISTRY:
                selected_profile = MODEL_REGISTRY[chosen_key]
            else:
                style_id = detect_style_from_checkpoint(actual_model_ckpt or str(chosen_style))
                selected_profile = MODEL_REGISTRY.get(style_id)

        if not router_profile and not selected_profile:
            if has_router:
                best_model_id, _ = self.router.match_prompt(pos_raw)
                router_profile = self.router.get_profile(best_model_id)
                actual_model_ckpt = (
                    router_profile.get("checkpoint")
                    if router_profile
                    else self.router.get_checkpoint_for_model(best_model_id)
                )
            if best_model_id and best_model_id in MODEL_REGISTRY:
                selected_profile = MODEL_REGISTRY[best_model_id]
            else:
                ckpt = actual_model_ckpt or best_model_id or ""
                style_id = detect_style_from_checkpoint(ckpt)
                selected_profile = MODEL_REGISTRY.get(style_id) or MODEL_REGISTRY.get("realistic", list(MODEL_REGISTRY.values())[0])

        if not selected_profile:
            ckpt_hint = actual_model_ckpt or (router_profile.get("checkpoint") if router_profile else "") or ""
            style_id = detect_style_from_checkpoint(ckpt_hint)
            selected_profile = MODEL_REGISTRY.get(style_id) or MODEL_REGISTRY.get("realistic", list(MODEL_REGISTRY.values())[0])

        canon = selected_profile.canon
        final_positive = sanitize_prompt(pos_raw)

        if actual_model_ckpt:
            target_ckpt = actual_model_ckpt
        elif router_profile and router_profile.get("checkpoint"):
            target_ckpt = router_profile["checkpoint"]
        elif chosen_style and str(chosen_style).lower().endswith(".safetensors"):
            target_ckpt = str(chosen_style)
        else:
            target_ckpt = selected_profile.canonical_filename
            if available_models:
                matched = find_checkpoint_for_style(selected_profile.id, available_models)
                if matched:
                    target_ckpt = matched

        if not router_profile and has_router and target_ckpt:
            router_profile = self.router.get_profile(target_ckpt)

        model_pipeline = (router_profile.get("pipeline") or {}) if isinstance(router_profile, dict) else {}
        plan_model_id = router_profile.get("id", selected_profile.id) if router_profile else selected_profile.id
        plan_model_title = router_profile.get("name", selected_profile.title) if router_profile else selected_profile.title

        # Негативный промпт (из профиля JSON или CLI --neg)
        if neg_raw:
            final_negative = sanitize_prompt(neg_raw)
        else:
            custom_neg_from_profile = ""
            if isinstance(router_profile, dict):
                custom_neg_from_profile = (
                    model_pipeline.get("negative_prompt")
                    or router_profile.get("negative_prompt")
                    or ""
                ).strip()
            elif has_router and hasattr(self.router, "profiles"):
                for p in self.router.profiles:
                    if p.get("id") == selected_profile.id or p.get("checkpoint") == target_ckpt:
                        custom_neg_from_profile = (
                            (p.get("pipeline") or {}).get("negative_prompt")
                            or p.get("negative_prompt")
                            or ""
                        ).strip()
                        if custom_neg_from_profile:
                            break

            final_negative = build_negative_prompt(
                pos_raw,
                selected_profile.id,
                canon.base_negative,
                archetype="solo",
                custom_base_neg=custom_neg_from_profile
            )

        # Определение архитектуры (сначала из профиля JSON, затем fallback по имени чекпоинта)
        if isinstance(router_profile, dict):
            model_target: Any = dict(router_profile)
            if target_ckpt and target_ckpt != model_target.get("checkpoint"):
                model_target["checkpoint"] = target_ckpt
                if is_xl_model(target_ckpt) or is_sdxl_checkpoint(target_ckpt):
                    model_target["architecture"] = "sdxl"
                elif model_target.get("architecture") == "sdxl" and not is_xl_model(target_ckpt):
                    model_target["architecture"] = "sd15"
        else:
            model_target = f"{target_ckpt} {selected_profile.id} {selected_profile.title}"

        is_xl = is_xl_model(model_target) or is_sdxl_checkpoint(target_ckpt)

        # Параметры инференса: CLI-флаги в приоритете -> затем pipeline из JSON -> затем canon
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
            enable_hr = False
            hr_scale = 1.0
        else:
            enable_hr = True
            hr_scale = float(user_params.get("hr_scale", model_pipeline.get("hr_scale", 1.5)))

        hr_denoise = float(user_params.get("denoising_strength", model_pipeline.get("hires_denoise", 0.35)))
        hr_upscaler = user_params.get("hr_upscaler", model_pipeline.get("hires_upscaler", canon.hr_upscaler or "R-ESRGAN 4x+"))
        hr_second_pass_steps = int(user_params.get("hr_second_pass_steps", model_pipeline.get("hr_second_pass_steps", canon.hr_second_pass_steps or 14)))

        ad_cfg = self._resolve_adetailer_pipeline(
            model_pipeline=model_pipeline,
            canon=canon,
            is_bg=False,
            is_xl=is_xl,
            wants_hands=False,
        )
        if "enable_adetailer" in user_params:
            enable_ad = bool(user_params["enable_adetailer"])
            hands_enabled = bool(ad_cfg["hands_enabled"]) if enable_ad else False
        else:
            enable_ad = bool(ad_cfg["enable_adetailer"])
            hands_enabled = bool(ad_cfg["hands_enabled"])

        default_w, default_h = (1024, 1024) if is_xl else (512, 768)
        width = int(user_params.get("width", default_w))
        height = int(user_params.get("height", default_h))

        logger.info(
            f"\\n"
            f"======================= [DIRECT ART: PURE DISPATCH] =======================\\n"
            f"[POSITIVE]  : \\"{final_positive}\\"\\n"
            f"[NEGATIVE]  : \\"{final_negative}\\"\\n"
            f"[CKPT]      : {target_ckpt} ({plan_model_id})\\n"
            f"[INFERENCE] : Steps={steps} | CFG={cfg_scale} | Sampler={sampler_name}/{scheduler} | ClipSkip={clip_skip} | Seed={effective_seed} | Size={width}x{height}\\n"
            f"[HIRES.FIX] : Enable={enable_hr} | Scale={hr_scale}x | Denoise={hr_denoise} | Upscaler={hr_upscaler}\\n"
            f"[ADETAILER] : Face={'ON' if enable_ad else 'OFF'} | Hands={'ON' if hands_enabled else 'OFF'}\\n"
            f"==========================================================================="
        )

        return GenerationPlan(
            style=plan_model_id,
            target_model_key=plan_model_id,
            model_title=plan_model_title,
            target_checkpoint=target_ckpt,
            positive_prompt=final_positive,
            negative_prompt=final_negative,
            width=width,
            height=height,
            steps=steps,
            cfg_scale=cfg_scale,
            sampler_name=sampler_name,
            scheduler=scheduler,
            enable_hr=enable_hr,
            hr_scale=hr_scale,
            hr_upscaler=hr_upscaler,
            denoising_strength=hr_denoise,
            hr_second_pass_steps=hr_second_pass_steps,
            clip_skip=clip_skip,
            enable_adetailer=enable_ad,
            enable_hand_adetailer=hands_enabled,
            ad_denoising_strength=ad_cfg["face_denoise"] if enable_ad else None,
            ad_prompt=final_positive if enable_ad else None,
            ad_negative_prompt=final_negative if enable_ad else None,
            ad_mask_blur=4,
            seed=effective_seed,
            hires_upscaler=hr_upscaler,
            hires_denoise=hr_denoise,
            reasoning="Прямой ручной инференс (!арт)",
            style_badge="⚡ Директ-арт",
            adetailer_face_model=ad_cfg["face_model"],
            adetailer_face_confidence=ad_cfg["face_confidence"],
            adetailer_face_denoise=ad_cfg["face_denoise"],
            adetailer_face_inpaint_size=ad_cfg["face_inpaint_size"],
            adetailer_inpaint_size=ad_cfg["face_inpaint_size"],
            adetailer_hands_enabled=hands_enabled,
            adetailer_hands_model=ad_cfg["hands_model"],
            adetailer_hands_confidence=ad_cfg["hands_confidence"],
            adetailer_hands_denoise=ad_cfg["hands_denoise"],
            adetailer_hands_inpaint_size=ad_cfg["hands_inpaint_size"],
            adetailer_hands_mask_blur=ad_cfg["hands_mask_blur"],
            adetailer_hands=hands_enabled,
            adetailer_hands_prompt=final_positive if hands_enabled else None,
            adetailer_hands_negative_prompt=final_negative if hands_enabled else None,
        )'''

updated_lf = text_lf[:start_idx] + new_method + text_lf[end_idx:]
updated_final = updated_lf.replace("\n", newline)
DIRECTOR_PATH.write_bytes(updated_final.encode(enc))
print("Patched director.py successfully.")

# Живая проверка уже пропатченного файла без подмен!
sys.path.insert(0, str(ROOT))
from services.model_router import ModelRouter
from services.art_director.director import ArtDirector

router = ModelRouter()
sig = inspect.signature(ArtDirector.__init__)
kwargs = {"router": router} if "router" in sig.parameters else ({"model_router": router} if "model_router" in sig.parameters else {})
director = ArtDirector(**kwargs)
if not getattr(director, "router", None):
    director.router = router

models_data = json.loads((ROOT / "data" / "art_director_models.json").read_text(encoding="utf-8-sig"))["models"]
available_models = [{"model_name": m["checkpoint"], "title": m["checkpoint"]} for m in models_data]

p_jug = director.build_direct_plan(
    "!арт Midshot, woman, dusk setting, outdoor",
    preferred_style="Juggernaut_XI_Light_byRunDiffusion.safetensors",
    available_models=available_models,
)
assert p_jug.target_checkpoint == "Juggernaut_XI_Light_byRunDiffusion.safetensors"
assert p_jug.model_title == "Juggernaut_XI_Light_byRunDiffusion"
assert (p_jug.width, p_jug.height) == (1024, 1024)
assert p_jug.steps == 6 and p_jug.cfg_scale == 2.0 and p_jug.clip_skip == 2
assert p_jug.sampler_name == "DPM++ 2M" and p_jug.scheduler == "Karras"
assert p_jug.enable_hr is False and p_jug.hr_upscaler == "4x_NMKD-Siax_200k"

p_cli = director.build_direct_plan(
    "!арт red car --steps 12 --cfg 3.5 --w 832 --h 1216 --hr on --scale 1.5 --ad on",
    preferred_style="Juggernaut_XI_Light_byRunDiffusion.safetensors",
    available_models=available_models,
)
assert (p_cli.width, p_cli.height) == (832, 1216) and p_cli.steps == 12 and p_cli.cfg_scale == 3.5
assert p_cli.enable_hr is True and p_cli.hr_scale == 1.5 and p_cli.enable_adetailer is True

for m in models_data:
    pm = director.build_direct_plan("!арт test scene", preferred_style=m["checkpoint"], available_models=available_models)
    exp = m["pipeline"]
    assert pm.target_checkpoint == m["checkpoint"]
    assert pm.steps == int(exp["steps"])
    assert pm.cfg_scale == float(exp["cfg_scale"])
    assert pm.enable_hr == bool(exp["enable_hr"])
    assert pm.enable_adetailer == bool(exp["enable_adetailer"])

print("LIVE DIRECTOR VERIFICATION PASSED FOR ALL 10 MODELS + CLI FLAGS!")
