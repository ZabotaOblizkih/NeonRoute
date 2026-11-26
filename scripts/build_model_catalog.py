"""
scripts/build_model_catalog.py
==============================
Deterministic generator and merger for data/model_presets_catalog.json.
Merges project defaults (MODEL_REGISTRY, DEFAULT_ART_DIRECTOR_MODELS, and optional local
art_director_models.json) into the curated catalog without overwriting curated fields:
- style_and_prompts (non-empty curated values are preserved)
- match.aliases, match.sha256, match.civitai_model_id, match.civitai_version_id
- curated pipeline fields
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.art_director_config import DEFAULT_ART_DIRECTOR_MODELS
from services.forge_models import MODEL_REGISTRY
from services.model_catalog_service import (
    CATALOG_SCHEMA_VERSION,
    DEFAULT_PIPELINE_PARAMS,
    compute_canonical_key,
    detect_architecture_from_name,
    merge_unique_str_list,
    safe_bool,
    safe_float,
    safe_int,
    safe_str,
    safe_str_list,
    split_sampler_scheduler,
    validate_catalog_schema,
)


def _build_entry_from_art_director_model(m: Dict[str, Any]) -> Dict[str, Any]:
    ckpt = safe_str(m.get("checkpoint"))
    m_id = safe_str(m.get("id"))
    name = safe_str(m.get("name"))
    pipe = dict(m.get("pipeline") or {})
    arch = safe_str(m.get("architecture") or pipe.get("architecture")) or detect_architecture_from_name(ckpt or name) or "sd15"
    canonical_key = compute_canonical_key(ckpt or name or m_id, architecture=arch)

    raw_sampler = pipe.get("sampler", DEFAULT_PIPELINE_PARAMS["sampler"])
    raw_sched = pipe.get("scheduler", DEFAULT_PIPELINE_PARAMS["scheduler"])
    sampler, scheduler = split_sampler_scheduler(raw_sampler, raw_sched)

    aliases = merge_unique_str_list(
        [Path(ckpt).stem] if ckpt else [],
        [name, m_id],
    )

    pipeline_obj: Dict[str, Any] = {
        "architecture": arch,
        "sampler": sampler or DEFAULT_PIPELINE_PARAMS["sampler"],
        "scheduler": scheduler or DEFAULT_PIPELINE_PARAMS["scheduler"],
        "steps": safe_int(pipe.get("steps"), DEFAULT_PIPELINE_PARAMS["steps"], 1, 150),
        "cfg_scale": round(safe_float(pipe.get("cfg_scale"), DEFAULT_PIPELINE_PARAMS["cfg_scale"], 1.0, 30.0), 1),
        "clip_skip": safe_int(pipe.get("clip_skip"), DEFAULT_PIPELINE_PARAMS["clip_skip"], 1, 12),
        "enable_hr": safe_bool(pipe.get("enable_hr"), False),
        "hr_scale": round(safe_float(pipe.get("hr_scale"), DEFAULT_PIPELINE_PARAMS["hr_scale"], 1.0, 4.0), 2),
        "hires_upscaler": safe_str(pipe.get("hires_upscaler"), DEFAULT_PIPELINE_PARAMS["hires_upscaler"]),
        "hires_denoise": round(safe_float(pipe.get("hires_denoise"), DEFAULT_PIPELINE_PARAMS["hires_denoise"], 0.05, 1.0), 2),
        "enable_adetailer": safe_bool(pipe.get("enable_adetailer"), True),
        "adetailer_face_model": safe_str(pipe.get("adetailer_face_model"), "face_yolov8m.pt"),
        "adetailer_face_confidence": round(safe_float(pipe.get("adetailer_face_confidence"), 0.44, 0.1, 1.0), 2),
        "adetailer_face_denoise": round(safe_float(pipe.get("adetailer_face_denoise"), 0.32, 0.05, 1.0), 2),
        "adetailer_face_inpaint_size": safe_int(pipe.get("adetailer_face_inpaint_size") or pipe.get("adetailer_inpaint_size"), 512, 256, 2048),
        "adetailer_inpaint_size": safe_int(pipe.get("adetailer_inpaint_size") or pipe.get("adetailer_face_inpaint_size"), 512, 256, 2048),
        "adetailer_hands_enabled": safe_bool(pipe.get("adetailer_hands_enabled"), True),
        "adetailer_hands_model": safe_str(pipe.get("adetailer_hands_model"), "hand_yolov9c.pt"),
        "adetailer_hands_confidence": round(safe_float(pipe.get("adetailer_hands_confidence"), 0.38, 0.1, 1.0), 2),
        "adetailer_hands_denoise": round(safe_float(pipe.get("adetailer_hands_denoise"), 0.40, 0.05, 1.0), 2),
        "adetailer_hands_inpaint_size": safe_int(pipe.get("adetailer_hands_inpaint_size"), 512, 256, 2048),
        "adetailer_hands_mask_blur": safe_int(pipe.get("adetailer_hands_mask_blur"), 8, 0, 64),
        "adetailer_hands_prompt": safe_str(pipe.get("adetailer_hands_prompt")),
        "adetailer_hands_negative_prompt": safe_str(pipe.get("adetailer_hands_negative_prompt")),
    }

    return {
        "match": {
            "canonical_key": canonical_key,
            "aliases": aliases,
            "civitai_model_id": None,
            "civitai_version_id": None,
            "sha256": None,
        },
        "style_and_prompts": {
            "routing_description": safe_str(m.get("routing_description") or m.get("description")),
            "llm_guidance": safe_str(m.get("llm_guidance")),
            "style_triggers": safe_str_list(m.get("style_triggers") or m.get("triggers")),
            "trained_words": safe_str_list(m.get("trained_words")),
            "negative_prompt": safe_str(pipe.get("negative_prompt") or m.get("negative_prompt")),
        },
        "pipeline": pipeline_obj,
    }


def _build_entry_from_registry_profile(profile: Any) -> Dict[str, Any]:
    ckpt = safe_str(getattr(profile, "canonical_filename", ""))
    arch = detect_architecture_from_name(ckpt or getattr(profile, "id", "")) or "sd15"
    canon = profile.canon
    sampler, scheduler = split_sampler_scheduler(canon.sampler_name, canon.scheduler)
    aliases = merge_unique_str_list(
        [Path(ckpt).stem] if ckpt else [],
        list(getattr(profile, "filename_aliases", ())) + [profile.id],
    )
    return {
        "match": {
            "canonical_key": compute_canonical_key(ckpt or profile.id, architecture=arch),
            "aliases": aliases,
            "civitai_model_id": None,
            "civitai_version_id": None,
            "sha256": None,
        },
        "style_and_prompts": {
            "routing_description": safe_str(getattr(profile, "description", "")),
            "llm_guidance": "",
            "style_triggers": [],
            "trained_words": [],
            "negative_prompt": safe_str(canon.base_negative),
        },
        "pipeline": {
            "architecture": arch,
            "sampler": sampler or canon.sampler_name,
            "scheduler": scheduler or canon.scheduler,
            "steps": safe_int(canon.recommended_steps, 25, 1, 150),
            "cfg_scale": round(safe_float(canon.default_cfg, 6.0, 1.0, 30.0), 1),
            "clip_skip": safe_int(canon.clip_skip, 1, 1, 12),
            "enable_hr": False,
            "hr_scale": round(safe_float(canon.default_hr_scale, 1.25, 1.0, 4.0), 2),
            "hires_upscaler": safe_str(canon.hr_upscaler, "4x-UltraSharp"),
            "hires_denoise": round(safe_float(canon.default_hr_denoise, 0.34, 0.05, 1.0), 2),
            "enable_adetailer": bool(canon.allow_adetailer),
            "adetailer_face_model": "face_yolov8m.pt",
            "adetailer_face_confidence": 0.44,
            "adetailer_face_denoise": round(safe_float(canon.default_ad_denoise, 0.35, 0.05, 1.0), 2),
            "adetailer_face_inpaint_size": 768 if arch == "sdxl" else 512,
            "adetailer_inpaint_size": 768 if arch == "sdxl" else 512,
            "adetailer_hands_enabled": True,
            "adetailer_hands_model": "hand_yolov9c.pt",
            "adetailer_hands_confidence": 0.38,
            "adetailer_hands_denoise": 0.40,
            "adetailer_hands_inpaint_size": 512,
            "adetailer_hands_mask_blur": 8,
            "adetailer_hands_prompt": "",
            "adetailer_hands_negative_prompt": "",
        },
    }


def merge_catalog_entry(
    existing: Optional[Dict[str, Any]],
    incoming: Dict[str, Any],
    prefer_incoming_pipeline: bool = False,
    explicit_incoming_pipeline_keys: Optional[set] = None,
) -> Dict[str, Any]:
    """
    Merges an incoming project entry into an existing catalog entry.
    - Never deletes existing curated fields when incoming fields are empty/None.
    - When prefer_incoming_pipeline=True (working profile from art_director_models.json),
      explicit non-empty pipeline settings from incoming update the catalog entry.
    """
    if not existing:
        return incoming

    ex_match = dict(existing.get("match") or {})
    in_match = dict(incoming.get("match") or {})
    merged_match = {
        "canonical_key": ex_match.get("canonical_key") or in_match.get("canonical_key"),
        "aliases": merge_unique_str_list(ex_match.get("aliases"), in_match.get("aliases")),
        "civitai_model_id": ex_match.get("civitai_model_id") if ex_match.get("civitai_model_id") is not None else in_match.get("civitai_model_id"),
        "civitai_version_id": ex_match.get("civitai_version_id") if ex_match.get("civitai_version_id") is not None else in_match.get("civitai_version_id"),
        "sha256": ex_match.get("sha256") or in_match.get("sha256"),
    }

    ex_style = dict(existing.get("style_and_prompts") or {})
    in_style = dict(incoming.get("style_and_prompts") or {})
    merged_style = {
        "routing_description": safe_str(ex_style.get("routing_description") or ex_style.get("description")) or safe_str(in_style.get("routing_description")),
        "llm_guidance": safe_str(ex_style.get("llm_guidance")) or safe_str(in_style.get("llm_guidance")),
        "style_triggers": merge_unique_str_list(ex_style.get("style_triggers"), in_style.get("style_triggers")),
        "trained_words": merge_unique_str_list(ex_style.get("trained_words"), in_style.get("trained_words")),
        "negative_prompt": safe_str(ex_style.get("negative_prompt")) or safe_str(in_style.get("negative_prompt")),
    }

    ex_pipe = dict(existing.get("pipeline") or {})
    in_pipe = dict(incoming.get("pipeline") or {})
    merged_pipe = dict(in_pipe)
    for k, v in ex_pipe.items():
        if v is not None and v != "":
            merged_pipe[k] = v

    if prefer_incoming_pipeline:
        for k, v in in_pipe.items():
            if explicit_incoming_pipeline_keys is not None and k not in explicit_incoming_pipeline_keys:
                continue
            if v is not None and v != "":
                merged_pipe[k] = v

    return {
        "match": merged_match,
        "style_and_prompts": merged_style,
        "pipeline": merged_pipe,
    }


def _find_matching_index(entries: List[Dict[str, Any]], incoming: Dict[str, Any]) -> Optional[int]:
    in_match = incoming.get("match") or {}
    in_key = safe_str(in_match.get("canonical_key")).lower()
    in_aliases = {a.lower() for a in safe_str_list(in_match.get("aliases"))}
    for idx, ex in enumerate(entries):
        ex_match = ex.get("match") or {}
        ex_key = safe_str(ex_match.get("canonical_key")).lower()
        if in_key and ex_key == in_key:
            return idx
        ex_aliases = {a.lower() for a in safe_str_list(ex_match.get("aliases"))}
        if in_aliases and ex_aliases and (in_aliases & ex_aliases):
            # Only match via aliases if architectures are compatible
            ex_arch = safe_str((ex.get("pipeline") or {}).get("architecture")).lower()
            in_arch = safe_str((incoming.get("pipeline") or {}).get("architecture")).lower()
            if not ex_arch or not in_arch or ex_arch == in_arch:
                return idx
    return None


def build_and_write_catalog(
    catalog_path: Path,
    art_director_models_path: Optional[Path] = None,
    include_local_art_models: bool = False,
) -> Dict[str, Any]:
    """Builds, merges, validates, and deterministically writes the model presets catalog."""
    existing_catalog: Dict[str, Any] = {}
    existing_models: List[Dict[str, Any]] = []

    if catalog_path.exists():
        raw = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
        ok, err = validate_catalog_schema(raw)
        if not ok:
            raise ValueError(f"Invalid existing catalog {catalog_path}: {err}")
        existing_catalog = raw
        existing_models = list(raw.get("models") or [])

    merged_entries: List[Dict[str, Any]] = list(existing_models)

    incoming_entries: List[Dict[str, Any]] = [
        _build_entry_from_registry_profile(prof) for prof in MODEL_REGISTRY.values()
    ]
    for dm in DEFAULT_ART_DIRECTOR_MODELS.get("models", []):
        incoming_entries.append(_build_entry_from_art_director_model(dm))

    for inc in incoming_entries:
        idx = _find_matching_index(merged_entries, inc)
        if idx is not None:
            merged_entries[idx] = merge_catalog_entry(merged_entries[idx], inc, prefer_incoming_pipeline=False)
        else:
            merged_entries.append(inc)

    if include_local_art_models and art_director_models_path and art_director_models_path.exists():
        try:
            local_data = json.loads(art_director_models_path.read_text(encoding="utf-8-sig"))
            for lm in local_data.get("models", []):
                if isinstance(lm, dict) and (lm.get("checkpoint") or lm.get("name")):
                    inc = _build_entry_from_art_director_model(lm)
                    explicit_keys = set((lm.get("pipeline") or {}).keys())
                    idx = _find_matching_index(merged_entries, inc)
                    if idx is not None:
                        merged_entries[idx] = merge_catalog_entry(
                            merged_entries[idx],
                            inc,
                            prefer_incoming_pipeline=True,
                            explicit_incoming_pipeline_keys=explicit_keys,
                        )
                    else:
                        merged_entries.append(inc)
        except Exception as e:
            print(f"[build_model_catalog] Warning: failed to read {art_director_models_path}: {e}", file=sys.stderr)

    merged_entries.sort(key=lambda e: safe_str((e.get("match") or {}).get("canonical_key")).lower())

    prev_serialized = json.dumps(existing_models, sort_keys=True, ensure_ascii=False)
    next_serialized = json.dumps(merged_entries, sort_keys=True, ensure_ascii=False)
    if prev_serialized == next_serialized and existing_catalog.get("generated_at"):
        generated_at = existing_catalog["generated_at"]
    else:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    output_catalog = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_at": generated_at,
        "models": merged_entries,
    }
    ok, err = validate_catalog_schema(output_catalog)
    if not ok:
        raise ValueError(f"Generated catalog failed schema validation: {err}")

    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = catalog_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(output_catalog, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(catalog_path)
    return output_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="Build/merge NeonRoute model presets catalog.")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=REPO_ROOT / "data" / "model_presets_catalog.json",
        help="Path to output model_presets_catalog.json",
    )
    parser.add_argument(
        "--include-local",
        action="store_true",
        help="Also merge entries from data/art_director_models.json",
    )
    args = parser.parse_args()

    result = build_and_write_catalog(
        catalog_path=args.catalog,
        art_director_models_path=REPO_ROOT / "data" / "art_director_models.json",
        include_local_art_models=args.include_local,
    )
    print(f"[build_model_catalog] Validated and wrote {len(result['models'])} entries to {args.catalog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
