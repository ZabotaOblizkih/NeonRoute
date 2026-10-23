from pathlib import Path
import config
from services.art_director.prompting import build_negative_prompt
from services.art_director.geometry import ASPECT_RATIO_BUCKETS, get_archetype_config
from services.entity_mapper import load_entities_dictionary, fallback_cyrillic_to_english, FALLBACK_ENTITY_MAP
import services.art_director as ad


def test_entities_json_files_absent_on_disk():
    assert not (config.BASE_DIR / "data" / "entities.json").exists()
    assert not (config.BASE_DIR / "services" / "data" / "entities.json").exists()


def test_entities_json_path_not_in_art_director():
    assert not hasattr(ad, "ENTITIES_JSON_PATH")
    assert "ENTITIES_JSON_PATH" not in ad.__all__


def test_geometry_aspect_ratio_buckets_extra_neg():
    for archetype, bucket in ASPECT_RATIO_BUCKETS.items():
        assert bucket.get("extra_neg") == "", f"Archetype {archetype} must have empty extra_neg"
    pair_cfg = get_archetype_config("realistic", "pair")
    assert pair_cfg.get("extra_negative") == ""


def test_entity_mapper_no_disk_access():
    flat_map, categories = load_entities_dictionary()
    assert isinstance(flat_map, dict)
    assert len(flat_map) > 0
    # Ensure fallback_cyrillic_to_english functions properly without disk access
    tags, is_ambiguous, unrecognized, matched_cats = fallback_cyrillic_to_english("девушка сидит на кровати")
    assert not is_ambiguous
    assert "1woman" in tags or "young woman" in tags


def test_build_negative_prompt_strictly_two_sources():
    # Prompt with nudity, animals, scenery, pair - previously triggered dynamic_tokens
    prompt = "2girls nude naked topless couple kissing landscape dog"

    # 1. Base model negative only (no user_neg)
    neg = build_negative_prompt(
        prompt=prompt,
        profile_id="anime",
        canon_base_neg="canon_test_negative",
        user_neg="",
        archetype="pair",
        custom_base_neg=""
    )
    assert neg == "canon_test_negative"
    # Ensure none of the old dynamic tokens were injected
    assert "clothes" not in neg
    assert "conjoined twins" not in neg
    assert "mosaic" not in neg
    assert "censored" not in neg
    assert "human" not in neg

    # 2. Base model negative + user_neg (--neg)
    neg_with_user = build_negative_prompt(
        prompt=prompt,
        profile_id="anime",
        canon_base_neg="canon_test_negative",
        user_neg="bad hands, extra fingers",
        archetype="pair",
        custom_base_neg=""
    )
    assert neg_with_user == "canon_test_negative, bad hands, extra fingers"

    # 3. Custom base negative takes precedence over canon_base_neg
    neg_custom = build_negative_prompt(
        prompt=prompt,
        profile_id="anime",
        canon_base_neg="canon_test_negative",
        user_neg="blurry",
        archetype="pair",
        custom_base_neg="custom_neg_test"
    )
    assert neg_custom == "custom_neg_test, blurry"
