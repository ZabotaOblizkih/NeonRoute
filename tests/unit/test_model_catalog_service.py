"""
tests/unit/test_model_catalog_service.py
========================================
Isolated unit tests for:
- Name normalization & collision-free canonical keys
- Unified Civitai metadata parser (None safety, Karras split, explicit Clip skip, empty_meta)
- Catalog loading/validation diagnostics (broken JSON != not_found)
- Cascade resolver (GitHub -> Local Catalog -> Civitai API -> Explicit Fallback)
- Deterministic catalog builder merge (scripts/build_model_catalog.py)
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import services.civitai_service as civitai_service
from services.model_catalog_service import (
    check_arch_compatibility,
    compute_canonical_key,
    load_local_catalog,
    normalize_model_search_candidates,
    parse_civitai_meta,
    resolve_model_presets,
)
from scripts.build_model_catalog import build_and_write_catalog


def test_normalization_candidates_and_canonical_key_no_collision():
    """Проверяет очистку технических суффиксов и отсутствие коллизий между версиями/архитектурами."""
    candidates = normalize_model_search_candidates("DreamShaper_8_pruned_fp16[a1b2c3].safetensors")
    assert candidates[0] == "DreamShaper 8 pruned fp16"
    assert "DreamShaper 8" in candidates
    assert "DreamShaper" in candidates

    k_v1_sd15 = compute_canonical_key("myModel_v1_fp16.safetensors", architecture="sd15")
    k_v2_sd15 = compute_canonical_key("myModel_v2_fp16.safetensors", architecture="sd15")
    k_v2_sdxl = compute_canonical_key("myModel_v2_fp16.safetensors", architecture="sdxl")

    assert k_v1_sd15 != k_v2_sd15
    assert k_v2_sd15 != k_v2_sdxl
    assert check_arch_compatibility("sdxl", "sd15") is False
    assert check_arch_compatibility("sdxl", "sdxl") is True
    assert check_arch_compatibility(None, "sd15") is True


def test_parse_civitai_meta_karras_clip_skip_and_none_safety():
    """Проверяет разделение Karras, защиту от None и сохранение явного Clip skip у аниме-моделей."""
    parsed = parse_civitai_meta(
        meta={
            "sampler": "DPM++ 2M Karras",
            "steps": None,
            "cfgScale": "7.5",
            "Clip skip": 1,
            "Denoising strength": "0.35",
            "negativePrompt": "bad art",
        },
        model_tags=["anime", "2d"],
        model_title="AnimeTest",
        checkpoint_name="animetest.safetensors",
        base_model="SD 1.5",
        trained_words=["1girl", "1girl", "solo"],
    )
    assert parsed["has_real_meta"] is True
    assert parsed["sampler"] == "DPM++ 2M"
    assert parsed["scheduler"] == "Karras"
    assert parsed["steps"] == 25  # fallback on None
    assert parsed["cfg_scale"] == 7.5
    assert parsed["clip_skip"] == 1  # explicit Clip skip=1 NOT overwritten to 2
    assert parsed["hires_denoise"] == 0.35
    assert parsed["architecture"] == "sd15"
    assert parsed["trained_words"] == ["1girl", "solo"]


def test_parse_civitai_meta_empty_returns_has_real_meta_false():
    """Пустой meta не должен помечаться как has_real_meta=True."""
    parsed = parse_civitai_meta(meta={}, model_title="Unknown", checkpoint_name="unknown.safetensors")
    assert parsed["has_real_meta"] is False


def test_local_catalog_broken_json_returns_diagnostic_error(tmp_path):
    """Поврежденный JSON каталога должен возвращать явную ошибку, а не маскироваться под not_found."""
    broken = tmp_path / "broken_catalog.json"
    broken.write_text("{invalid json", encoding="utf-8")

    data, err = load_local_catalog(broken)
    assert data is None
    assert err is not None and "Ошибка чтения/парсинга" in err


def test_cascade_resolver_github_then_local_then_civitai_then_fallback(tmp_path, monkeypatch):
    """Проверяет строгий каскад: GitHub -> Local -> Civitai -> Explicit Fallback."""
    local_cat_path = tmp_path / "model_presets_catalog.json"
    local_cat_path.write_text(
        json.dumps({
            "schema_version": 1,
            "generated_at": "2026-11-25T00:00:00Z",
            "models": [
                {
                    "match": {
                        "canonical_key": "sd15::local_model",
                        "aliases": ["local_model"],
                        "civitai_model_id": None,
                        "civitai_version_id": None,
                        "sha256": None,
                    },
                    "style_and_prompts": {
                        "routing_description": "Local desc",
                        "llm_guidance": "tag1, tag2",
                        "style_triggers": ["local"],
                        "trained_words": ["triggerA"],
                        "negative_prompt": "local neg",
                    },
                    "pipeline": {
                        "architecture": "sd15",
                        "sampler": "Euler a",
                        "scheduler": "Normal",
                        "steps": 22,
                        "cfg_scale": 5.5,
                        "clip_skip": 1,
                        "enable_hr": False,
                        "hr_scale": 1.25,
                        "hires_upscaler": "4x-UltraSharp",
                        "hires_denoise": 0.3,
                        "enable_adetailer": True,
                    },
                }
            ],
        }),
        encoding="utf-8",
    )

    # 1. GitHub hit
    gh_resp = MagicMock()
    gh_resp.status_code = 200
    gh_resp.json.return_value = {
        "schema_version": 1,
        "generated_at": "2026-11-25T00:00:00Z",
        "models": [
            {
                "match": {"canonical_key": "sd15::local_model", "aliases": ["local_model"]},
                "style_and_prompts": {"negative_prompt": "gh neg"},
                "pipeline": {"architecture": "sd15", "sampler": "DPM++ 2M", "scheduler": "Karras", "steps": 30, "cfg_scale": 6.5, "clip_skip": 1},
            }
        ],
    }
    monkeypatch.setattr("services.model_catalog_service.requests.get", lambda *a, **kw: gh_resp)
    res_gh = resolve_model_presets("local_model.safetensors", catalog_url="https://example.test/cat.json", catalog_path=local_cat_path)
    assert res_gh["status"] == "success"
    assert res_gh["source"] == "github_catalog"
    assert res_gh["steps"] == 30

    # 2. GitHub fails -> Local catalog hit
    def _fail_gh(*a, **kw):
        raise TimeoutError("offline")
    monkeypatch.setattr("services.model_catalog_service.requests.get", _fail_gh)
    res_local = resolve_model_presets("local_model.safetensors", catalog_url="https://example.test/cat.json", catalog_path=local_cat_path)
    assert res_local["status"] == "success"
    assert res_local["source"] == "local_catalog"
    assert res_local["sampler"] == "Euler a"
    assert res_local["trained_words"] == ["triggerA"]

    # 3. Local miss -> Civitai API empty meta -> status == "empty_meta" (NOT "success")
    monkeypatch.setattr(
        "services.civitai_service.fetch_civitai_api_parameters",
        lambda **kw: {"status": "empty_meta", "source": "civitai", "message": "empty"},
    )
    res_empty = resolve_model_presets("unknown_model.safetensors", allow_github=False, catalog_path=local_cat_path)
    assert res_empty["status"] == "empty_meta"

    # 4. Local miss + Civitai miss -> explicit fallback not_found
    monkeypatch.setattr(
        "services.civitai_service.fetch_civitai_api_parameters",
        lambda **kw: {"status": "not_found", "source": "fallback", "message": "missing"},
    )
    res_nf = resolve_model_presets("unknown_model.safetensors", allow_github=False, catalog_path=local_cat_path)
    assert res_nf["status"] == "not_found"
    assert res_nf["source"] == "fallback"


def test_civitai_service_has_no_hardcoded_forge_dir():
    """Убеждается, что жесткий путь FORGE_SD_MODELS_DIR удален из civitai_service."""
    assert not hasattr(civitai_service, "FORGE_SD_MODELS_DIR")


def test_build_model_catalog_deterministic_merge_preserves_curated(tmp_path):
    """Генератор каталога сохраняет кураторские поля и пишет детерминированный JSON."""
    cat_path = tmp_path / "model_presets_catalog.json"
    curated = {
        "schema_version": 1,
        "generated_at": "2026-11-25T00:00:00Z",
        "models": [
            {
                "match": {
                    "canonical_key": "sd15::dreamshaper_8",
                    "aliases": ["custom_ds_alias"],
                    "civitai_model_id": 4384,
                    "civitai_version_id": 128713,
                    "sha256": "DEADBEEF",
                },
                "style_and_prompts": {
                    "routing_description": "Curated DreamShaper description",
                    "llm_guidance": "curated_tag",
                    "style_triggers": ["curated_trigger"],
                    "trained_words": ["curated_word"],
                    "negative_prompt": "curated_neg",
                },
                "pipeline": {
                    "architecture": "sd15",
                    "sampler": "DPM++ SDE",
                    "scheduler": "Karras",
                    "steps": 33,
                    "cfg_scale": 7.5,
                    "clip_skip": 2,
                },
            }
        ],
    }
    cat_path.write_text(json.dumps(curated), encoding="utf-8")

    result = build_and_write_catalog(catalog_path=cat_path, include_local_art_models=False)
    ds_entry = next(m for m in result["models"] if m["match"]["canonical_key"] == "sd15::dreamshaper_8")

    assert ds_entry["match"]["sha256"] == "DEADBEEF"
    assert "custom_ds_alias" in ds_entry["match"]["aliases"]
    assert ds_entry["style_and_prompts"]["routing_description"] == "Curated DreamShaper description"
    assert ds_entry["style_and_prompts"]["trained_words"] == ["curated_word"]
    assert ds_entry["pipeline"]["steps"] == 33


def test_build_model_catalog_append_upsert_never_deletes_entries(tmp_path):
    """
    Проверяет накопительный (append/upsert) режим каталога:
    - Сценарий 1: в каталоге A и B, в art_director_models.json только B (с обновлёнными шагами) и C -> остаются A, B (обновлённая), C; кураторские поля B не затираются пустыми значениями.
    - Сценарий 2: art_director_models.json очищен ({"models": []}) -> A, B, C сохраняются в каталоге без потерь.
    """
    cat_path = tmp_path / "model_presets_catalog.json"
    art_path = tmp_path / "art_director_models.json"

    initial_catalog = {
        "schema_version": 1,
        "generated_at": "2026-11-25T00:00:00Z",
        "models": [
            {
                "match": {"canonical_key": "sd15::model_a", "aliases": ["model_a"], "civitai_model_id": 101, "civitai_version_id": None, "sha256": "AAA111"},
                "style_and_prompts": {"routing_description": "Desc A", "llm_guidance": "tagA", "style_triggers": ["a"], "trained_words": ["twA"], "negative_prompt": "negA"},
                "pipeline": {"architecture": "sd15", "sampler": "Euler a", "scheduler": "Normal", "steps": 20, "cfg_scale": 6.0, "clip_skip": 1},
            },
            {
                "match": {"canonical_key": "sdxl::model_b", "aliases": ["model_b"], "civitai_model_id": 202, "civitai_version_id": None, "sha256": "BBB222"},
                "style_and_prompts": {"routing_description": "Curated Desc B", "llm_guidance": "curatedB", "style_triggers": ["b"], "trained_words": ["twB"], "negative_prompt": "Curated Neg B"},
                "pipeline": {"architecture": "sdxl", "sampler": "DPM++ 2M", "scheduler": "Karras", "steps": 25, "cfg_scale": 5.5, "clip_skip": 2},
            },
        ],
    }
    cat_path.write_text(json.dumps(initial_catalog), encoding="utf-8")

    # Сценарий 1: A удалена из art_director_models.json, B обновлена (steps=32, пустой negative_prompt), C добавлена
    art_path.write_text(
        json.dumps({
            "models": [
                {
                    "id": "model_b",
                    "name": "Model B",
                    "architecture": "sdxl",
                    "checkpoint": "model_b.safetensors",
                    "routing_description": "",
                    "llm_guidance": "",
                    "negative_prompt": "",
                    "style_triggers": ["b_new"],
                    "pipeline": {"architecture": "sdxl", "sampler": "DPM++ 2M", "scheduler": "Karras", "steps": 32, "cfg_scale": 5.0, "clip_skip": 2},
                },
                {
                    "id": "model_c",
                    "name": "Model C",
                    "architecture": "sd15",
                    "checkpoint": "model_c.safetensors",
                    "routing_description": "Desc C",
                    "llm_guidance": "tagC",
                    "negative_prompt": "negC",
                    "style_triggers": ["c"],
                    "pipeline": {"architecture": "sd15", "sampler": "Euler a", "scheduler": "Karras", "steps": 28, "cfg_scale": 7.0, "clip_skip": 1},
                },
            ]
        }),
        encoding="utf-8",
    )

    res1 = build_and_write_catalog(catalog_path=cat_path, art_director_models_path=art_path, include_local_art_models=True)
    by_key1 = {m["match"]["canonical_key"]: m for m in res1["models"]}
    assert "sd15::model_a" in by_key1
    assert "sdxl::model_b" in by_key1
    assert "sd15::model_c" in by_key1
    assert by_key1["sd15::model_a"]["style_and_prompts"]["routing_description"] == "Desc A"
    assert by_key1["sdxl::model_b"]["pipeline"]["steps"] == 32
    assert by_key1["sdxl::model_b"]["style_and_prompts"]["routing_description"] == "Curated Desc B"
    assert by_key1["sdxl::model_b"]["style_and_prompts"]["negative_prompt"] == "Curated Neg B"
    assert by_key1["sdxl::model_b"]["style_and_prompts"]["style_triggers"] == ["b", "b_new"]

    # Сценарий 2: art_director_models.json полностью очищен -> A, B, C сохраняются без потерь
    art_path.write_text('{"models": []}', encoding="utf-8")
    res2 = build_and_write_catalog(catalog_path=cat_path, art_director_models_path=art_path, include_local_art_models=True)
    by_key2 = {m["match"]["canonical_key"]: m for m in res2["models"]}
    assert "sd15::model_a" in by_key2
    assert "sdxl::model_b" in by_key2
    assert "sd15::model_c" in by_key2
    assert by_key2["sdxl::model_b"]["pipeline"]["steps"] == 32
    assert by_key2["sd15::model_c"]["style_and_prompts"]["routing_description"] == "Desc C"
