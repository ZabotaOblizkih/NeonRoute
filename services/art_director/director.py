import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Union

from services.forge_models import (
    MODEL_REGISTRY,
    ModelProfile,
    sanitize_prompt,
    find_checkpoint_for_style,
    detect_style_from_checkpoint,
)
from services.direct_command_parser import parse_direct_command_params
from services.art_director.lora_manager import LoraManager, extract_lora_metadata
from services.art_director.vram_policy import (
    is_hires_safe,
    resolve_hardware_vram_budget,
    calculate_max_hires_canvas_area,
    resolve_forge_offload_policy,
    resolve_checkpoint_file_size_mb,
    resolve_safe_hires_scale,
    resolve_hires_second_pass_steps,
    format_vram_planner_log,
)
from services.forge_client import is_sdxl_checkpoint
from services.model_router import ModelRouter

from .contracts import TargetSubject, VisualIntent
from .plan import GenerationPlan
from .geometry import (
    ASPECT_RATIO_BUCKETS,
    detect_scene_archetype,
    get_archetype_config,
    is_xl_model,
)
from .prompting import (
    ELARIA_VISUAL_DNA,
    build_negative_prompt,
    parse_user_negative_trigger,
    parse_image_prompt_flags,
    extract_negations_from_prompt,
)

logger = logging.getLogger("NeonRoute.ArtDirector")

# Helper markers for detecting weapons/gear in inventory:
WEAPON_TAG_MAP = {
    "катана": "holding katana",
    "меч": "holding sword",
    "клинок": "holding blade",
    "кинжал": "holding dagger",
    "нож": "holding knife",
    "лук": "holding bow",
    "посох": "holding staff",
    "пистолет": "holding pistol",
    "винтовка": "holding rifle",
    "щит": "holding shield",
    "факел": "holding torch",
    "фонарь": "holding lantern",
    "амулет": "amulet",
    "шлем": "helmet",
    "плащ": "cloak",
}

PEACEFUL_SCENE_MARKERS = (
    "sleep", "sleeping", "bed", "bedroom", "bath", "bathing", "hot spring",
    "onsen", "rest", "resting", "kiss", "kissing", "hug", "embrace",
    "cuddle", "nude", "naked", "underwear", "nightgown", "chemise",
    "сон", "спать", "кровать", "ванна", "купание", "отдых", "поцелуй", "объятия",
)

INSTRUCTION_META_NOISE = {
    "only architecture", "architecture", "room type", "furniture",
    "materials", "lighting", "weather", "outdoor scenery",
    "room", "environment", "tags", "indoor", "outdoor",
}


def _clean_context_subtag(text: Optional[str]) -> str:
    """Тотально удаляет кавычки, дефисы списков и пробелы из строк контекста."""
    if not text or not isinstance(text, str):
        return ""
    clean = text.replace("'", "").replace('"', '').replace('`', '')
    clean = re.sub(r'^[\s\-*•]+', '', clean)
    clean = re.sub(r',\s*,+', ',', clean)
    return clean.strip(" ,-")


def tagify_prompt_with_lm(text: str, lm_client: Optional[Any] = None) -> str:
    """
    Преобразует русский текст в Danbooru-теги через LM Studio.
    Изолирует флаги (--ar, --neg, --model), переводит описание и склеивает обратно.
    """
    if not text or not re.search(r'[\u0400-\u04ff]', text):
        return text

    # 1. Извлечение и срез флагов
    flags = []
    def _extract_flag(match):
        flags.append(match.group(0).strip())
        return ""

    clean_text = re.sub(r'--(?:ar|neg|model|style)\s+["\']?[a-zA-Z0-9_\-]+["\']?', _extract_flag, text)
    clean_text = clean_text.strip(" ,")

    if not clean_text or not re.search(r'[\u0400-\u04ff]', clean_text):
        return text

    if lm_client is None:
        clean_no_cyr = re.sub(r'[\u0400-\u04ff]+', '', clean_text).strip(' ,')
        clean_no_cyr = re.sub(r'\s{2,}', ' ', clean_no_cyr).strip(' ,')
        return f"{clean_no_cyr}, {' '.join(flags)}".strip(' ,') if flags else clean_no_cyr

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert Stable Diffusion prompt tagifier. "
                "Convert the user's description into concise, comma-separated English Danbooru/SD positive tags. "
                "Output ONLY the comma-separated tags. Do NOT write sentences, explanations, or quotes."
            )
        },
        {"role": "user", "content": clean_text}
    ]

    try:
        from services.regex_filter import TextRegexFilter
        raw = lm_client.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=60,
            stream=False,
            emit_signal=False,
            enable_thinking=False,
            cache_prompt=False,
            id_slot=None,
        )
        raw_clean = TextRegexFilter.strip_thought_blocks(str(raw or "")).strip()
        if not raw_clean:
            clean_no_cyr = re.sub(r'[\u0400-\u04ff]+', '', clean_text).strip(' ,')
            clean_no_cyr = re.sub(r'\s{2,}', ' ', clean_no_cyr).strip(' ,')
            return f"{clean_no_cyr}, {' '.join(flags)}".strip(' ,') if flags else clean_no_cyr

        tagified = re.sub(r'^(?:Tags|Prompt|Positive Prompt)[:\s]*', '', raw_clean, flags=re.I)
        tagified = tagified.replace('`', '').replace('"', '').strip(' ,')

        # 2. Если кириллица осталась (сбой модели) — аварийно отсекаем русские токены
        if re.search(r'[\u0400-\u04ff]', tagified):
            tagified = re.sub(r'[^a-zA-Z0-9_, -]', '', tagified).strip(' ,')

        if not tagified:
            clean_no_cyr = re.sub(r'[\u0400-\u04ff]+', '', clean_text).strip(' ,')
            clean_no_cyr = re.sub(r'\s{2,}', ' ', clean_no_cyr).strip(' ,')
            return f"{clean_no_cyr}, {' '.join(flags)}".strip(' ,') if flags else clean_no_cyr

        result = f"{tagified}, {' '.join(flags)}".strip(' ,') if flags else tagified
        return result
    except Exception:
        # Fallback: отсекаем кириллицу во избежание поломки CLIP
        clean_no_cyr = re.sub(r'[\u0400-\u04ff]+', '', clean_text).strip(' ,')
        clean_no_cyr = re.sub(r'\s{2,}', ' ', clean_no_cyr).strip(' ,')
        return f"{clean_no_cyr}, {' '.join(flags)}".strip(' ,') if flags else clean_no_cyr


def _distribute_inventory_gear(
    inventory_items: List[str],
    is_peaceful: bool = False,
    focus_name: str = "",
    secondary_name: str = "",
    party_comps: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Распределение экипировки по слотам персонажей.
    """
    if is_peaceful or not inventory_items:
        return [], []

    focus_tags: List[str] = []
    sec_tags: List[str] = []

    focus_names = [focus_name.lower().strip()] if focus_name else []
    sec_names = [secondary_name.lower().strip()] if secondary_name else []
    if party_comps:
        for ent in party_comps:
            c_name = ent.get("canonical_name", "").lower().strip()
            aliases = [a.lower().strip() for a in ent.get("aliases", []) if isinstance(a, str) and a.strip()]
            all_n = [c_name] + aliases
            if focus_name and c_name == focus_name.lower().strip():
                focus_names.extend(all_n)
            elif secondary_name and c_name == secondary_name.lower().strip():
                sec_names.extend(all_n)

    focus_names = [n for n in set(focus_names) if len(n) >= 3]
    sec_names = [n for n in set(sec_names) if len(n) >= 3]

    unassigned_tags: List[str] = []

    for item in inventory_items:
        itm_low = str(item).lower().strip()
        matched_tag = None
        for marker, tag in WEAPON_TAG_MAP.items():
            if marker in itm_low:
                matched_tag = tag
                break
        if not matched_tag:
            continue

        belongs_to_sec = any(sn in itm_low for sn in sec_names)
        belongs_to_focus = any(fn in itm_low for fn in focus_names)

        if belongs_to_sec and not belongs_to_focus:
            if matched_tag not in sec_tags:
                sec_tags.append(matched_tag)
        elif belongs_to_focus:
            if matched_tag not in focus_tags:
                focus_tags.append(matched_tag)
        else:
            if matched_tag not in unassigned_tags:
                unassigned_tags.append(matched_tag)

    for ut in unassigned_tags:
        if secondary_name:
            if not focus_tags:
                focus_tags.append(ut)
            elif not sec_tags:
                sec_tags.append(ut)
            elif ut not in focus_tags:
                focus_tags.append(ut)
        else:
            if ut not in focus_tags:
                focus_tags.append(ut)

    return focus_tags, sec_tags


def _resolve_inventory_gear(inventory_items: List[str], is_peaceful: bool = False) -> List[str]:
    if is_peaceful:
        return []
    tags: List[str] = []
    for item in inventory_items:
        itm_low = str(item).lower().strip()
        for marker, tag in WEAPON_TAG_MAP.items():
            if marker in itm_low and tag not in tags:
                tags.append(tag)
                break
    return tags


def _symmetrize_break_chunks(full_pos: str, loc_suffix: str, light_suffix: str) -> str:
    """Синхронизация окружения для мульти-персонажей через BREAK."""
    if " BREAK " not in full_pos:
        return full_pos

    chunks = full_pos.split(" BREAK ")
    common_tokens = []
    if loc_suffix and loc_suffix.strip():
        common_tokens.append(loc_suffix.strip().strip(","))
    if light_suffix and light_suffix.strip():
        common_tokens.append(light_suffix.strip().strip(","))

    common_env_str = ", ".join(common_tokens).strip(" ,")
    if not common_env_str:
        return full_pos

    symmetrized_chunks = []
    for chunk in chunks:
        ch_clean = chunk.strip().strip(",")
        if loc_suffix and loc_suffix.split(",")[0].strip().lower() not in ch_clean.lower():
            ch_clean = f"{ch_clean}, {common_env_str}".strip(" ,")
        symmetrized_chunks.append(ch_clean)

    return " BREAK ".join(symmetrized_chunks)


def _mix_guidance_tags(prompt: str, guidance: str, max_tags: int = 8) -> str:
    """
    Разбивает llm_guidance на отдельные теги по запятым, берет первые max_tags (6-8) тегов
    и подмешивает их к prompt (только те теги, которых еще нет в prompt).
    """
    if not guidance or not isinstance(guidance, str):
        return prompt

    raw_tags = [t.strip() for t in guidance.split(",") if t.strip()]
    if not raw_tags:
        return prompt

    selected_tags = raw_tags[:max_tags]

    prompt_clean = prompt.strip(" ,") if prompt else ""
    prompt_lower = prompt_clean.lower()
    existing_tags = {t.strip().lower() for t in re.split(r'[,|]', prompt_lower) if t.strip()}

    tags_to_add = []
    seen = set()
    for tag in selected_tags:
        t_clean = tag.strip()
        t_low = t_clean.lower()
        if not t_clean or t_low in seen:
            continue
        seen.add(t_low)
        if t_low in existing_tags:
            continue
        if re.search(rf'(?:\b|^){re.escape(t_low)}(?:\b|$)', prompt_lower):
            continue
        tags_to_add.append(t_clean)

    if not tags_to_add:
        return prompt

    mixed_chunk = ", ".join(tags_to_add)
    if not prompt_clean:
        return mixed_chunk

    return f"{prompt_clean}, {mixed_chunk}".strip(" ,")


def _assemble_roleplay_context(
    base_prompt: str,
    session_id: Optional[str],
    character_name: Optional[str],
    character: Optional[Any],
    intent_companion_dna: Optional[str] = None,
    intent_companion_outfit: Optional[str] = None,
    intent_location_dna: Optional[str] = None,
    intent_mutations: Optional[List[str]] = None,
    is_closeup_frame: bool = False
) -> Dict[str, Any]:
    """
    СТРОГО ДЛЯ РЕЖИМА РОЛЕВОГО ЧАТА (ROLEPLAY):
    Возвращает dict с ключами:
    - prompt: собранный промпт
    - focus_name: имя основного персонажа
    - secondary_name: имя второго персонажа (если есть)
    - has_secondary: bool
    - loc_dna: активная локация
    - cast_data: загруженный слепок каста
    """
    cast_data = {}
    if session_id:
        try:
            from services.rp_cast_manager import RPCastManager
            cast_data = RPCastManager.load_cast(session_id, companion_name=character_name or "Элария")
        except Exception as e_cast:
            logger.debug(f"[ArtDirector] Could not load cast: {e_cast}")

    focus_name = cast_data.get("focus_companion", character_name or "Элария")

    # Layer 1: Companion Visual DNA
    comp_dna = intent_companion_dna
    if not comp_dna and cast_data:
        comp_dna = (cast_data.get("companion_state") or {}).get("visual_dna")
    if not comp_dna and character:
        comp_dna = getattr(character, "visual_dna", None)
    comp_dna = _clean_context_subtag(comp_dna)

    # Layer 2: Outfit
    comp_outfit = _clean_context_subtag(intent_companion_outfit)
    if not comp_outfit and cast_data:
        comp_outfit = _clean_context_subtag((cast_data.get("companion_state") or {}).get("current_outfit"))

    # Определение вторичного компаньона
    secondary_dna = ""
    secondary_name = ""
    pos_lower = base_prompt.lower()
    party_comps = [e for e in cast_data.get("entities", {}).values() if isinstance(e, dict) and e.get("role") == "companion"]

    for ent in party_comps:
        c_name = ent.get("canonical_name", "")
        if c_name.lower().strip() != focus_name.lower().strip():
            aliases = [c_name.lower().strip()] + [a.lower().strip() for a in ent.get("aliases", []) if isinstance(a, str) and a.strip()]
            if any(re.search(rf"\b{re.escape(al)}\b", pos_lower) if len(al) >= 3 else (al in pos_lower) for al in aliases if al):
                secondary_dna = _clean_context_subtag(ent.get("visual_dna") or (ent.get("companion_state") or {}).get("visual_dna"))
                if secondary_dna:
                    secondary_name = c_name
                    break

    multi_char_pattern = r'\b(2girls|2boys|1girl 1boy|duo|trio|group|multiple girls|multiple boys)\b|\bbreak\b'
    is_multi_tag = bool(re.search(multi_char_pattern, pos_lower))

    is_group_scene = (
        (is_multi_tag or len(party_comps) > 1 or bool(character and getattr(character, "is_group", False)))
        and not any(re.search(rf'\b{k}\b', pos_lower) for k in ("solo", "1girl", "1boy"))
    )

    if is_group_scene and not secondary_dna:
        for ent in party_comps:
            c_name = ent.get("canonical_name", "")
            if c_name.lower().strip() != focus_name.lower().strip():
                secondary_dna = _clean_context_subtag(ent.get("visual_dna") or (ent.get("companion_state") or {}).get("visual_dna"))
                if secondary_dna:
                    secondary_name = c_name
                    break

    # Мирная сцена и экипировка
    current_loc = _clean_context_subtag(cast_data.get("current_location"))
    is_nude = any(w in pos_lower for w in ("nude", "naked", "bare body", "topless", "bottomless"))
    is_peaceful = is_nude or any(m in pos_lower or m in current_loc.lower() for m in PEACEFUL_SCENE_MARKERS)

    raw_inv = cast_data.get("inventory", [])
    clean_inv = [_clean_context_subtag(str(it)) for it in raw_inv if it] if raw_inv else []
    focus_gear, sec_gear = _distribute_inventory_gear(
        clean_inv,
        is_peaceful=is_peaceful,
        focus_name=focus_name,
        secondary_name=secondary_name,
        party_comps=party_comps
    )

    full_pos = base_prompt
    if is_group_scene or secondary_dna:
        full_pos = re.sub(r'\b(solo)\b', '', full_pos, flags=re.I).strip(" ,")
        if not any(k in full_pos.lower() for k in ("2girls", "duo", "pair")):
            full_pos = f"2girls, {full_pos}".strip(" ,")

    lead_tokens = []
    if comp_dna:
        lead_tokens.append(comp_dna)
    if comp_outfit and not is_nude:
        lead_tokens.append(comp_outfit)
    if focus_gear:
        for fg in focus_gear:
            if fg.lower() not in full_pos.lower():
                lead_tokens.append(fg)

    if secondary_dna:
        sec_outfit = ""
        for ent in party_comps:
            if ent.get("canonical_name", "").lower().strip() == secondary_name.lower().strip():
                sec_outfit = _clean_context_subtag((ent.get("companion_state") or {}).get("current_outfit"))
                break
        sec_chunk = secondary_dna
        if sec_outfit and not is_nude:
            sec_chunk = f"{sec_chunk}, {sec_outfit}"
        if sec_gear:
            sec_gear_add = [sg for sg in sec_gear if sg.lower() not in full_pos.lower()]
            if sec_gear_add:
                sec_chunk = f"{sec_chunk}, {', '.join(sec_gear_add)}"
        lead_tokens.append(f"BREAK {sec_chunk}")

    if lead_tokens:
        full_pos = f"{', '.join(lead_tokens)}, {full_pos}".strip(" ,")

    # Мутации
    muts = intent_mutations or []
    if not muts and session_id:
        p_muts = (cast_data.get("player_state") or {}).get("mutations", [])
        c_muts = (cast_data.get("companion_state") or {}).get("mutations", [])
        muts = list(p_muts + c_muts)
    if not is_peaceful and muts:
        for m in muts:
            m_en = _clean_context_subtag(str(m))
            if m_en and m_en.lower() not in full_pos.lower():
                full_pos = f"{full_pos}, {m_en}".strip(" ,")

    # Локация окружения с защитой от дубликатов
    env = cast_data.get("environment", {}) if isinstance(cast_data, dict) else {}
    loc_active = _clean_context_subtag(intent_location_dna or env.get("location_dna") or current_loc)
    if not loc_active and character:
        raw_char_scenery = getattr(character, "scenery_dna", None)
        if isinstance(raw_char_scenery, str) and raw_char_scenery.strip():
            clean_tags = [
                t.strip() for t in raw_char_scenery.split(",")
                if t.strip() and t.strip().lower() not in INSTRUCTION_META_NOISE
            ]
            loc_active = _clean_context_subtag(", ".join(clean_tags))

    if loc_active:
        # [STUB] Автоинъекция локации отключена: LLM сама генерирует окружение в кадре.
        # Предотвращает дублирование тегов и раздувание промпта свыше 77 токенов.
        pass

    full_pos = _symmetrize_break_chunks(full_pos, loc_active, "")

    return {
        "prompt": full_pos,
        "focus_name": focus_name,
        "secondary_name": secondary_name,
        "has_secondary": bool(secondary_dna),
        "loc_dna": loc_active,
        "cast_data": cast_data
    }


class ModelRegistrySelector:
    """Селектор профилей моделей из реестра MODEL_REGISTRY."""
    def __init__(self, art_director: 'ArtDirector'):
        self.art_director = art_director

    def select_profile(self, style_or_key: Any) -> ModelProfile:
        if isinstance(style_or_key, str) and style_or_key in MODEL_REGISTRY:
            return MODEL_REGISTRY[style_or_key]
        return MODEL_REGISTRY.get("realistic", list(MODEL_REGISTRY.values())[0])


class ArtDirector:
    def __init__(self):
        self.registry = ModelRegistrySelector(self)
        self.router = ModelRouter()
        self.next_seed: int = -1

    def reload_models(self) -> None:
        """Перезагружает конфигурацию моделей из файла настроек без блокировки GUI."""
        from services.forge_models import sync_registry_with_user_config
        sync_registry_with_user_config()
        if hasattr(self, "router") and self.router is not None:
            self.router.load_profiles()
            self.router.update_embeddings_async(delay=0.0)

    def match_model_by_vector(self, query: str, active_models: Optional[List[Dict[str, Any]]] = None) -> Tuple[str, float]:
        """Возвращает (winner_title, score) через роутер моделей (SSOT для UI и генерации)."""
        if hasattr(self, "router") and self.router is not None:
            model_id, score = self.router.match_prompt(query, profiles=active_models)
            pool = active_models if active_models is not None else getattr(self.router, "profiles", [])
            for p in pool:
                if p.get("id") == model_id:
                    return p.get("name", model_id), score
            return model_id, score
        return ("realistic", 1.0)

    @staticmethod
    def _resolve_adetailer_pipeline(
        model_pipeline: Dict[str, Any],
        canon: Any,
        is_bg: bool,
        is_xl: bool,
        wants_hands: bool = False,
    ) -> Dict[str, Any]:
        """Единая точка резолвинга параметров ADetailer для расчёта VRAM и финального payload."""
        if "enable_adetailer" in model_pipeline and model_pipeline["enable_adetailer"] is not None:
            base_ad = bool(model_pipeline["enable_adetailer"])
        else:
            base_ad = bool(getattr(canon, "allow_adetailer", True))

        enable_ad = bool(base_ad or wants_hands) and not is_bg
        hands_on = (bool(model_pipeline.get("adetailer_hands_enabled", True)) or wants_hands) and enable_ad

        from services.art_director.vram_policy import resolve_hardware_vram_budget
        total_vram_gb, _ = resolve_hardware_vram_budget()
        default_inpaint = 512 if (not is_xl or (total_vram_gb and total_vram_gb <= 8.5)) else 768
        face_inpaint_sz = int(
            model_pipeline.get("adetailer_face_inpaint_size")
            or model_pipeline.get("adetailer_inpaint_size")
            or default_inpaint
        )
        hands_inpaint_sz = int(model_pipeline.get("adetailer_hands_inpaint_size", 512))

        return {
            "enable_adetailer": enable_ad,
            "face_model": model_pipeline.get("adetailer_face_model", "face_yolov8m.pt"),
            "face_confidence": float(model_pipeline.get("adetailer_face_confidence", 0.44)),
            "face_denoise": float(model_pipeline.get("adetailer_face_denoise", 0.35 if is_xl else 0.32)),
            "face_inpaint_size": face_inpaint_sz,
            "hands_enabled": hands_on,
            "hands_model": model_pipeline.get("adetailer_hands_model", "hand_yolov9c.pt"),
            "hands_confidence": float(model_pipeline.get("adetailer_hands_confidence", 0.38)),
            "hands_denoise": float(model_pipeline.get("adetailer_hands_denoise", 0.38 if is_xl else 0.40)),
            "hands_inpaint_size": hands_inpaint_sz,
            "hands_mask_blur": int(model_pipeline.get("adetailer_hands_mask_blur", 8)),
            "ad_prompt": None,
            "ad_negative_prompt": None,
            "hands_prompt": None,
            "hands_negative_prompt": None,
        }

    @staticmethod
    def _format_adetailer_log_blocks(plan: GenerationPlan) -> Tuple[str, str]:
        """Форматирует диагностические строки для слотов Face и Hands ADetailer."""
        if not getattr(plan, "enable_adetailer", False):
            return "OFF", "OFF"

        pos_val = getattr(plan, "prompt", None) or getattr(plan, "positive_prompt", "") or ""
        neg_val = getattr(plan, "negative_prompt", "") or ""
        ad_p = getattr(plan, "ad_prompt", None) or pos_val
        ad_neg = getattr(plan, "ad_negative_prompt", None) or neg_val

        # Определение источника Face
        face_src = getattr(plan, "ad_source", None)
        if not face_src:
            if ad_p == "[SKIP]":
                face_src = "json"
            elif getattr(plan, "ad_prompt", None) is not None and plan.ad_prompt != pos_val:
                face_src = "json"
            else:
                face_src = "fallback"

        face_model = getattr(plan, "adetailer_face_model", "none")
        face_conf = getattr(plan, "adetailer_face_confidence", 0.0)
        face_denoise = getattr(plan, "adetailer_face_denoise", 0.0)
        face_inpaint_sz = getattr(plan, "adetailer_face_inpaint_size", getattr(plan, "adetailer_inpaint_size", 512))

        if ad_p == "[SKIP]":
            ad_face_info = f"SKIPPED [src={face_src}] | Model=None"
        else:
            face_p = f'"{ad_p}"' if ad_p else '""'
            face_neg = f'"{ad_neg}"' if ad_neg else '""'
            ad_face_info = (
                f"ON [src={face_src}] | Model={face_model} | Conf={face_conf} | "
                f"Denoise={face_denoise} | Inpaint={face_inpaint_sz}px\n"
                f"   └── Prompt: {face_p}\n"
                f"   └── Neg   : {face_neg}"
            )

        ad_hands_info = "OFF"
        if getattr(plan, "adetailer_hands_enabled", False):
            h_p_val = getattr(plan, "adetailer_hands_prompt", None) or pos_val
            h_neg_val = getattr(plan, "adetailer_hands_negative_prompt", None) or neg_val

            # Определение источника Hands
            hands_src = getattr(plan, "adetailer_hands_source", None)
            if not hands_src:
                if h_p_val == "[SKIP]":
                    hands_src = "json"
                elif getattr(plan, "adetailer_hands_prompt", None) is not None and plan.adetailer_hands_prompt != pos_val:
                    hands_src = "json"
                else:
                    hands_src = "fallback"

            h_sz = getattr(plan, "adetailer_hands_inpaint_size", 512)
            h_model = getattr(plan, "adetailer_hands_model", "none")
            h_conf = getattr(plan, "adetailer_hands_confidence", 0.0)
            h_denoise = getattr(plan, "adetailer_hands_denoise", 0.0)
            h_blur = getattr(plan, "adetailer_hands_mask_blur", 8)

            if h_p_val == "[SKIP]":
                ad_hands_info = f"SKIPPED [src={hands_src}] | Model=None"
            else:
                h_p = f'"{h_p_val}"' if h_p_val else '""'
                h_neg = f'"{h_neg_val}"' if h_neg_val else '""'
                ad_hands_info = (
                    f"ON [src={hands_src}] | Model={h_model} | Conf={h_conf} | "
                    f"Denoise={h_denoise} | Inpaint={h_sz}px | Blur={h_blur}\n"
                    f"   └── Prompt: {h_p}\n"
                    f"   └── Neg   : {h_neg}"
                )
        return ad_face_info, ad_hands_info

    def _log_vram_planner_passport(
        self,
        plan: GenerationPlan,
        architecture: str,
        lora_count: int = 0,
    ) -> None:
        """Выводит в лог единую диагностическую строку [VRAM-Planner] по параметрам плана."""
        try:
            from config import FORGE_ENABLE_VAE_TILING
            from utils.config_manager import ConfigManager

            cfg = ConfigManager.load_config()
            sdxl_fp8_enabled = bool(cfg.get("forge_sdxl_fp8_enabled", True))
            ckpt = getattr(plan, "target_checkpoint", "") or ""
            is_xl = "xl" in str(architecture).lower() or is_sdxl_checkpoint(ckpt)
            is_fp8 = bool(is_xl and sdxl_fp8_enabled)
            inpaint_sz = max(
                int(getattr(plan, "adetailer_face_inpaint_size", 512) or 512),
                int(getattr(plan, "adetailer_hands_inpaint_size", 512) or 512)
                if getattr(plan, "adetailer_hands_enabled", False)
                else 512,
            )
            monitor = getattr(self, "system_monitor", None)
            total_vram_gb, _ = resolve_hardware_vram_budget(monitor)
            hr_on = bool(getattr(plan, "enable_hr", False))
            plan_w = int(getattr(plan, "width", 512) or 512)
            plan_h = int(getattr(plan, "height", 512) or 512)
            hr_scale_val = float(getattr(plan, "hr_scale", 1.0) or 1.0)
            hr_area = int(round(plan_w * plan_h * (hr_scale_val ** 2)))
            vae_tile = bool(
                FORGE_ENABLE_VAE_TILING
                or (is_xl and (total_vram_gb <= 8.5 or hr_on))
                or (not is_xl and hr_on and hr_area > 1_300_000)
            )
            pol = resolve_forge_offload_policy(
                width=int(getattr(plan, "width", 512) or 512),
                height=int(getattr(plan, "height", 512) or 512),
                architecture="sdxl" if is_xl else "sd15",
                is_fp8=is_fp8,
                enable_adetailer=bool(getattr(plan, "enable_adetailer", False)),
                active_lora_count=lora_count,
                system_monitor=monitor,
                enable_hr=bool(getattr(plan, "enable_hr", False)),
                hr_scale=float(getattr(plan, "hr_scale", 1.0) or 1.0),
                hr_upscaler=str(getattr(plan, "hr_upscaler", None) or getattr(plan, "hires_upscaler", None) or "R-ESRGAN 4x+"),
                enable_hand_adetailer=bool(getattr(plan, "adetailer_hands_enabled", False)),
                ad_inpaint_size=inpaint_sz,
                vae_tiling=vae_tile,
                checkpoint_size_mb=resolve_checkpoint_file_size_mb(ckpt),
            )
            if pol.get("force_disable_hr") and getattr(plan, "enable_hr", False):
                plan.enable_hr = False
                plan.hr_scale = 1.0
            if pol.get("disable_secondary_adetailer") and getattr(plan, "adetailer_hands_enabled", False):
                plan.enable_hand_adetailer = False
                plan.adetailer_hands_enabled = False
                plan.adetailer_hands = False
            if pol.get("host_ram_starved") and (
                pol.get("force_fp8_storage") or pol.get("disable_secondary_adetailer") or pol.get("force_disable_hr")
            ):
                logger.warning(
                    "[Host-RAM-Guard] Дефицит системной RAM + малый VRAM: отключены Hires.fix и второй слот ADetailer, "
                    "рекомендован даунгрейд UNet в fp8e4."
                )
            logger.info(format_vram_planner_log(pol))
        except Exception as e:
            logger.debug(f"[VRAM-Planner] Ошибка расчёта паспорта: {e}")

    def _resolve_hires_policy_for_plan(
        self,
        width: int,
        height: int,
        architecture: str,
        checkpoint: str = "",
        enable_adetailer: bool = False,
        enable_hand_adetailer: bool = False,
        requested_enable_hr: bool = False,
        requested_hr_scale: float = 1.0,
        hr_upscaler: str = "R-ESRGAN 4x+",
        ad_inpaint_size: int = 512,
        active_lora_count: int = 0,
    ) -> Dict[str, Any]:
        """Единый адаптивный резолвер политики Hires.fix с выводом диагностического паспорта."""
        from config import FORGE_ENABLE_VAE_TILING
        from utils.config_manager import ConfigManager
        from services.art_director.vram_policy import (
            DEFAULT_FORGE_RESERVE_MB,
            estimate_active_model_weights_mb,
        )

        cfg = ConfigManager.load_config()
        sdxl_fp8_enabled = bool(cfg.get("forge_sdxl_fp8_enabled", True))
        is_xl = "xl" in str(architecture).lower() or is_sdxl_checkpoint(checkpoint)
        is_fp8 = bool(is_xl and sdxl_fp8_enabled)
        ckpt_mb = resolve_checkpoint_file_size_mb(checkpoint)

        monitor = getattr(self, "system_monitor", None)
        total_vram_gb, os_reserve_mb = resolve_hardware_vram_budget(monitor)
        usable_vram_mb = max(0, int(round(total_vram_gb * 1024.0)) - int(os_reserve_mb) - DEFAULT_FORGE_RESERVE_MB)
        hr_area_est = int(round(width * height * (float(requested_hr_scale or 1.0) ** 2)))
        vae_tile = bool(
            FORGE_ENABLE_VAE_TILING
            or (is_xl and (total_vram_gb <= 8.5 or float(requested_hr_scale or 1.0) > 1.0))
            or (not is_xl and float(requested_hr_scale or 1.0) > 1.0 and hr_area_est > 1_300_000)
        )

        model_mb = estimate_active_model_weights_mb(
            architecture="sdxl" if is_xl else "sd15",
            is_fp8=is_fp8,
            checkpoint_size_mb=ckpt_mb,
        )

        ad_label = (
            "Face+Hands" if (enable_adetailer and enable_hand_adetailer)
            else ("Face" if enable_adetailer else "OFF")
        )

        req_scale = float(requested_hr_scale or 1.0)
        if not requested_enable_hr or req_scale <= 1.0:
            allowed = False
            eff_scale = 1.0
            hr_w, hr_h = width, height
            max_area = 0
            reason = "отключено в профиле (scale <= 1.0)"
            res = {
                "allowed": False,
                "effective_scale": 1.0,
                "requested_scale": req_scale,
                "hr_width": width,
                "hr_height": height,
                "max_area": 0,
                "policy": None,
                "reason": reason,
            }
        else:
            res = resolve_safe_hires_scale(
                width=width,
                height=height,
                requested_hr_scale=req_scale,
                architecture="sdxl" if is_xl else "sd15",
                is_fp8=is_fp8,
                enable_adetailer=enable_adetailer,
                enable_hand_adetailer=enable_hand_adetailer,
                active_lora_count=active_lora_count,
                hr_upscaler=hr_upscaler,
                vae_tiling=vae_tile,
                checkpoint_size_mb=ckpt_mb,
                system_monitor=monitor,
                ad_inpaint_size=ad_inpaint_size,
            )
            allowed = bool(res.get("allowed", False))
            eff_scale = float(res.get("effective_scale", 1.0)) if allowed else 1.0
            hr_w = int(res.get("hr_width", width)) if allowed else width
            hr_h = int(res.get("hr_height", height)) if allowed else height
            max_area = int(res.get("max_area", 0))
            reason = str(res.get("reason", ""))

        base_area = width * height
        ratio = max(width / height, height / width) if (height > 0 and width > 0) else 1.0
        hr_area = hr_w * hr_h
        arch_str = "SDXL" if is_xl else "SD15"

        logger.info(
            f"[Forge VRAM Policy Diagnostic Passport]\n"
            f"  ├── 1. Аппаратный профиль : Total VRAM = {total_vram_gb:.1f} GB | Резерв ОС = {os_reserve_mb} MB | Usable = {usable_vram_mb} MB\n"
            f"  ├── 2. Пайплайн и модель  : Arch = {arch_str} | FP8 = {is_fp8} ({model_mb} MB) | ADetailer = {ad_label} | VAETile = {vae_tile}\n"
            f"  ├── 3. Базовый холст      : {width}x{height} ({base_area:,} px) | Ratio = {ratio:.2f} | Потолок Hires = {max_area:,} px\n"
            f"  └── 4. Вердикт Hires.fix  : Allowed = {allowed} | Scale: {req_scale:.2f}x -> {eff_scale:.2f}x ({hr_w}x{hr_h}, {hr_area:,} px) | Причина: {reason}"
        )

        return res

    def _is_resolution_safe_for_hires(
        self,
        width: int,
        height: int,
        architecture: str,
        checkpoint: str = "",
        enable_adetailer: bool = False,
        hr_scale: float = 1.0,
    ) -> bool:
        """Динамическая проверка безопасности Hires.fix (сохранена для обратной совместимости)."""
        from utils.config_manager import ConfigManager
        from config import FORGE_ENABLE_VAE_TILING

        config = ConfigManager.load_config()
        sdxl_fp8_enabled = bool(config.get("forge_sdxl_fp8_enabled", True))
        is_xl = "xl" in str(architecture).lower() or is_sdxl_checkpoint(checkpoint)
        is_fp8 = bool(is_xl and sdxl_fp8_enabled)

        monitor = getattr(self, "system_monitor", None)
        total_vram_gb, os_reserve_mb = resolve_hardware_vram_budget(monitor)
        hr_area_est = int(round(width * height * (float(hr_scale or 1.0) ** 2)))
        vae_tile = bool(
            FORGE_ENABLE_VAE_TILING
            or (is_xl and (total_vram_gb <= 8.5 or float(hr_scale or 1.0) > 1.0))
            or (not is_xl and float(hr_scale or 1.0) > 1.0 and hr_area_est > 1_300_000)
        )

        return is_hires_safe(
            width=width,
            height=height,
            architecture="sdxl" if is_xl else "sd15",
            is_fp8=is_fp8,
            total_vram_gb=total_vram_gb,
            os_reserve_mb=os_reserve_mb,
            enable_adetailer=enable_adetailer,
            hr_scale=hr_scale,
            checkpoint_size_mb=resolve_checkpoint_file_size_mb(checkpoint),
            vae_tiling=vae_tile,
        )

    def plan_from_visual_intent(
        self,
        visual_intent: Union[Dict[str, Any], VisualIntent],
        preferred_style: Optional[str] = None,
        available_models: Optional[List[Dict[str, Any]]] = None,
        seed: Optional[int] = None,
        character_name: Optional[str] = None,
        character: Optional[Any] = None,
        session_id: Optional[str] = None,
        mode: Optional[str] = None,
        companion_dna: Optional[str] = None
    ) -> GenerationPlan:
        """
        Метод прямого планирования по готовому Message-Centric Visual Payload:
        - Полное исключение LLM (0 секунд задержки перед рендером, никаких вызовов lm_client).
        - Разрешение модели:
          Приоритет 1 (Ручной выбор пользователя): если передан preferred_style и он не начинается с '✨' / 'default' -> использовать его.
          Приоритет 2 (Рекомендация экстрактора): иначе, если visual_intent.get('suggested_model') -> использовать этот стиль.
          Приоритет 3 (Векторный фоллбэк FastEmbed): иначе -> self.router.match_prompt(visual_intent['prompt']).
        - Формирование промпта: base_prompt + мутации (если еще не включены) + Visual DNA + LoRA resolving.
        - СТРОГОЕ применение настроек из art_director_models.json:
          Считывание параметров steps, sampler, cfg_scale, clip_skip, hires_upscaler, hires_denoise, negative_prompt
          СТРОГО из блока pipeline выбранной модели.
        - Возвращает GenerationPlan (< 3 мс на CPU).
        """
        intent = VisualIntent.from_dict(visual_intent) if not isinstance(visual_intent, VisualIntent) else visual_intent

        effective_seed = seed if (seed is not None and seed != -1) else intent.seed
        if effective_seed == -1:
            effective_seed = self.next_seed
        self.next_seed = -1

        # 1. Разрешение модели (SSOT)
        chosen_style = None
        if preferred_style and not preferred_style.startswith("✨") and preferred_style.lower() not in ("default", "none", "auto"):
            chosen_style = preferred_style
        elif intent.suggested_model and str(intent.suggested_model).lower() not in ("auto", "none", "default"):
            chosen_style = intent.suggested_model
        else:
            base_p = (intent.prompt or "").strip()
            best_id, score = self.router.match_prompt(base_p)
            logger.info(f"[ArtDirector] Router match: '{base_p[:60]}' -> winner='{best_id}' (score: {score:.4f})")
            chosen_style = best_id

        # Определение чекпоинта и профиля
        router_profile = None
        if chosen_style and hasattr(self, "router") and self.router:
            # 1. Сначала точное совпадение по name == chosen_style или id == chosen_style
            if hasattr(self.router, "profiles"):
                cs_clean = str(chosen_style).strip().lower()
                for p in self.router.profiles:
                    p_name = str(p.get("name", "")).strip().lower()
                    p_id = str(p.get("id", "")).strip().lower()
                    if cs_clean and (cs_clean == p_name or cs_clean == p_id):
                        router_profile = p
                        break

            # 2. Если не найдено — поиск через get_profile
            if not router_profile and hasattr(self.router, "get_profile"):
                prof = self.router.get_profile(chosen_style)
                if isinstance(prof, dict):
                    router_profile = prof

        actual_model_ckpt = router_profile.get("checkpoint") if router_profile else None
        if not actual_model_ckpt and chosen_style and hasattr(self, "router") and self.router:
            actual_model_ckpt = self.router.get_checkpoint_for_model(chosen_style)

        if chosen_style and chosen_style.lower() in MODEL_REGISTRY:
            selected_profile = MODEL_REGISTRY[chosen_style.lower()]
        else:
            style_id = detect_style_from_checkpoint(actual_model_ckpt or chosen_style or "")
            selected_profile = MODEL_REGISTRY.get(style_id) or MODEL_REGISTRY.get("realistic", list(MODEL_REGISTRY.values())[0])

        canon = selected_profile.canon
        target_ckpt = actual_model_ckpt or (preferred_style if preferred_style and preferred_style.endswith(".safetensors") else selected_profile.canonical_filename)
        if not router_profile and target_ckpt:
            router_profile = self.router.get_profile(target_ckpt) if hasattr(self.router, "get_profile") else None
            if not isinstance(router_profile, dict):
                router_profile = None
        if not router_profile and hasattr(self, "router") and self.router and hasattr(self.router, "profiles"):
            target_ids = {chosen_style, getattr(selected_profile, "id", None)}
            target_names = {str(chosen_style).lower(), str(getattr(selected_profile, "title", "")).lower()}
            for p in self.router.profiles:
                if (p.get("id") in target_ids) or (target_ckpt and p.get("checkpoint") == target_ckpt):
                    router_profile = p
                    break
                p_name = p.get("name", "").lower()
                if p_name and p_name in target_names:
                    router_profile = p
                    break
                if target_ckpt and p.get("checkpoint"):
                    t_stem = Path(target_ckpt).stem.lower().replace("final", "").replace("_v5", "").replace("v5", "")
                    p_stem = Path(p["checkpoint"]).stem.lower().replace("final", "").replace("_v5", "").replace("v5", "")
                    if t_stem and p_stem and (t_stem in p_stem or p_stem in t_stem):
                        router_profile = p
                        break

        # 2. Формирование позитивного промпта и явного негатива с перехватом отрицаний
        raw_intent_prompt = (intent.prompt or "").strip()
        lora_pattern = re.compile(r'<lora:[^>]+>', re.IGNORECASE)
        intent_loras = lora_pattern.findall(raw_intent_prompt)
        clean_intent_text = lora_pattern.sub('', raw_intent_prompt).strip(' ,')

        parsed_intent_flags = parse_image_prompt_flags(clean_intent_text)
        has_self_flag = parsed_intent_flags.is_self_portrait
        extracted_ar_flag = parsed_intent_flags.aspect_ratio
        clean_intent_text = parsed_intent_flags.prompt
        if parsed_intent_flags.negative and not intent.user_neg:
            intent_neg_extra = parsed_intent_flags.negative
        else:
            intent_neg_extra = ""

        clean_intent_text = re.sub(r'[\(\[]\s*lora:[a-zA-Z0-9_\-.]+(?::[0-9.]+)?\s*[\)\]]', '', clean_intent_text, flags=re.IGNORECASE).strip(' ,')
        clean_intent_text = re.sub(r'(?<!-)\b(?:v[0-9]+(?:\.[0-9]+)?|final|pruned|fp16)\b$', '', clean_intent_text, flags=re.IGNORECASE).strip(' ,')

        raw_base = _clean_context_subtag(str(clean_intent_text))
        base_prompt, auto_neg_extracted = extract_negations_from_prompt(raw_base)
        user_neg_from_intent = _clean_context_subtag(str(intent.user_neg or intent_neg_extra or ""))
        if auto_neg_extracted:
            extra_auto = ", ".join(auto_neg_extracted)
            user_neg_from_intent = f"{extra_auto}, {user_neg_from_intent}".strip(" ,") if user_neg_from_intent else extra_auto

        if intent_loras:
            full_positive = f"{', '.join(intent_loras)}, {base_prompt}".strip(' ,')
        else:
            full_positive = base_prompt

        # Определение режима и персонажа
        eff_session_id = session_id or intent.session_id
        eff_mode = mode or intent.mode or ("roleplay" if (character is not None or (character_name and character_name.lower() not in ("элария", "elaria"))) else "assistant")

        is_assistant = (eff_mode == "assistant")
        has_female_markers = bool(re.search(r'\b(1girl|woman|girl|female|lady|maid|queen|princess|model|face|portrait)\b', full_positive, re.I))
        has_elaria_name = bool(re.search(r'\b(elaria|элария)\b', full_positive, re.I))
        is_elaria = is_assistant and (
            intent.target_subject == TargetSubject.ELARIA
            or has_self_flag
            or has_elaria_name
            or (character_name and character_name.lower() in ("элария", "elaria") and has_female_markers)
        )
        archetype = detect_scene_archetype(full_positive)

        focus_name = ""
        secondary_companion_name = ""
        has_secondary = False
        cast_data = {}
        loc_dna_active = ""

        if is_assistant:
            if intent.mutations:
                for mut in intent.mutations:
                    if mut and str(mut).strip() and str(mut).strip().lower() not in full_positive.lower():
                        full_positive = f"{full_positive}, {str(mut).strip()}".strip(" ,")
            if is_elaria and archetype != "scenery":
                full_positive = re.sub(r'\b(elaria|элария)\b', '', full_positive, flags=re.I).strip(' ,')
                full_positive = f"{ELARIA_VISUAL_DNA}, {full_positive}".strip(' ,')
        else:
            is_closeup_frame = (archetype == "closeup" or (intent.aspect_ratio and str(intent.aspect_ratio).lower() in ("closeup", "square", "1:1", "sq")))
            rp_res = _assemble_roleplay_context(
                base_prompt=full_positive,
                session_id=eff_session_id,
                character_name=character_name,
                character=character,
                intent_companion_dna=companion_dna or getattr(intent, "companion_dna", None),
                intent_companion_outfit=getattr(intent, "companion_outfit", None),
                intent_location_dna=getattr(intent, "location_dna", None),
                intent_mutations=intent.mutations,
                is_closeup_frame=is_closeup_frame
            )
            full_positive = rp_res["prompt"]
            focus_name = rp_res["focus_name"]
            secondary_companion_name = rp_res["secondary_name"]
            has_secondary = rp_res["has_secondary"]
            loc_dna_active = rp_res["loc_dna"]
            cast_data = rp_res["cast_data"]
            archetype = detect_scene_archetype(full_positive)

        # Подмешивание тегов llm_guidance из профиля модели к full_positive (первые 6-8 тегов)
        llm_guidance = ""
        if router_profile and isinstance(router_profile, dict):
            llm_guidance = router_profile.get("llm_guidance") or ""
        if not llm_guidance and selected_profile:
            if isinstance(selected_profile, dict):
                llm_guidance = selected_profile.get("llm_guidance") or ""
            else:
                llm_guidance = getattr(selected_profile, "llm_guidance", "") or ""

        if llm_guidance:
            full_positive = _mix_guidance_tags(full_positive, llm_guidance, max_tags=8)

        # Резолвинг LoRA (персонажи + micro-detail)
        effective_char = character_name
        if not effective_char and character:
            effective_char = getattr(character, "name", None) if not isinstance(character, str) else character

        char_names_for_lora: List[str] = []
        if is_assistant:
            if is_elaria:
                char_names_for_lora.append("Элария")
        else:
            if effective_char:
                char_names_for_lora.append(str(effective_char))
            if focus_name and focus_name not in char_names_for_lora:
                char_names_for_lora.append(focus_name)
            if secondary_companion_name and secondary_companion_name not in char_names_for_lora:
                char_names_for_lora.append(secondary_companion_name)

        lora_char_arg = ", ".join(char_names_for_lora) if char_names_for_lora else None

        lora_tags, act_tags = LoraManager.resolve_loras(
            prompt=full_positive,
            character_name=lora_char_arg,
            style_id=selected_profile.id,
            target_model_name=target_ckpt
        )
        tokens = [full_positive]
        if act_tags:
            new_acts = [a for a in act_tags if a.lower() not in full_positive.lower()]
            if new_acts:
                tokens.insert(0, ", ".join(new_acts))
        if lora_tags:
            new_loras = [l for l in lora_tags if l.lower() not in full_positive.lower()]
            tokens.extend(new_loras)
        final_positive = ", ".join(t.strip(" ,") for t in tokens if t.strip(" ,"))

        # Отделяем LoRA от описательного тела перед санитизацией
        all_loras_in_prompt = lora_pattern.findall(final_positive)
        clean_body_no_lora = lora_pattern.sub('', final_positive).strip(' ,')

        clean_prompt = clean_body_no_lora
        clean_prompt = re.sub(r'(?<!<)\b(?:\(?\[?)lora:[a-zA-Z0-9_\-.]+(?::[0-9.]+)?(?:\)?\]?)(?!>)', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')
        clean_body_no_lora = re.sub(r'[\(\[]\s*lora:[a-zA-Z0-9_\-.]+(?::[0-9.]+)?\s*[\)\]]', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')
        clean_body_no_lora = re.sub(r'(?<!-)\b(?:v[0-9]+(?:\.[0-9]+)?|final|pruned|fp16)\b$', '', clean_body_no_lora, flags=re.IGNORECASE).strip(' ,')

        prefix = canon.prompt_prefix.strip(' ,') if canon.prompt_prefix else ""
        if prefix and prefix.lower() not in clean_body_no_lora.lower():
            assembled_body = f"{prefix}, {clean_body_no_lora}".strip(' ,')
        else:
            assembled_body = clean_body_no_lora

        assembled_body = sanitize_prompt(assembled_body)
        clean_prompt = assembled_body

        # LoRA гарантированно на 1-й позиции
        if all_loras_in_prompt:
            seen_loras = set()
            unique_loras = []
            for l_tag in all_loras_in_prompt:
                l_key = l_tag.lower().replace(" ", "")
                if l_key not in seen_loras:
                    seen_loras.add(l_key)
                    unique_loras.append(l_tag)
            final_positive = f"{', '.join(unique_loras)}, {assembled_body}".strip(' ,')
        else:
            unique_loras = []
            final_positive = assembled_body

        # 3. СТРОГОЕ применение настроек из art_director_models.json
        model_pipeline = router_profile.get("pipeline", {}) if router_profile else {}
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

        steps = int(model_pipeline.get("steps", canon.recommended_steps))
        cfg_scale = float(model_pipeline.get("cfg_scale", canon.default_cfg))
        clip_skip = int(model_pipeline.get("clip_skip", canon.clip_skip))

        # Разрешение пропорций и архетипа: прямое подчинение флагу --ar
        ar_override = intent.aspect_ratio or extracted_ar_flag
        ar_clean = str(ar_override).strip().lower() if ar_override else None

        if ar_clean in ("portrait", "vertical", "tall", "2:3"):
            archetype = "solo"
        elif ar_clean in ("wide", "landscape", "horizontal", "3:2"):
            archetype = "pair"
        elif ar_clean in ("square", "sq", "1:1", "closeup"):
            archetype = "closeup"
        elif ar_clean in ("fullbody", "9:16"):
            archetype = "fullbody"
        elif ar_clean in ("cinematic", "pano", "16:9"):
            archetype = "cinematic"
        elif ar_clean in ("ultrawide", "wide21:9", "21:9", "anamorphic"):
            archetype = "ultrawide"
        elif ar_clean == "scenery":
            archetype = "scenery"
        elif ar_clean in ASPECT_RATIO_BUCKETS:
            archetype = ar_clean
        else:
            fp_low = final_positive.lower()
            is_pair_candidate = bool(has_secondary or "2girls" in fp_low)
            pair_layout_markers = ("side by side", "duo", " break ", "together", "couple")
            if is_pair_candidate and any(m in fp_low for m in pair_layout_markers):
                archetype = "pair"
            else:
                detected = detect_scene_archetype(final_positive)
                archetype = archetype if archetype != "solo" else (detected or "solo")

        if router_profile and isinstance(router_profile, dict):
            model_target = dict(router_profile)
            if target_ckpt:
                if not model_target.get("checkpoint") or target_ckpt != model_target.get("checkpoint"):
                    if is_xl_model(target_ckpt):
                        model_target["architecture"] = "sdxl"
                        model_target["checkpoint"] = target_ckpt
                    elif not is_xl_model(target_ckpt) and model_target.get("architecture") == "sdxl":
                        model_target["architecture"] = "sd15"
                        model_target["checkpoint"] = target_ckpt
                    else:
                        model_target["checkpoint"] = target_ckpt
        else:
            model_target = router_profile or f"{target_ckpt} {selected_profile.id} {selected_profile.title}"
        arch_cfg = get_archetype_config(model_target, archetype)
        width = arch_cfg["width"]
        height = arch_cfg["height"]

        # Настройка Hi-Res Fix и единый резолвинг ADetailer (SSOT)
        is_bg = bool(intent.is_scene_background)
        _is_xl_v = bool(arch_cfg.get("is_xl") or "xl" in str(arch_cfg.get("architecture", "")).lower() or is_sdxl_checkpoint(target_ckpt))
        ad_cfg = self._resolve_adetailer_pipeline(
            model_pipeline=model_pipeline,
            canon=canon,
            is_bg=is_bg,
            is_xl=_is_xl_v,
            wants_hands=False,
        )
        enable_ad = ad_cfg["enable_adetailer"]
        hands_enabled = ad_cfg["hands_enabled"]

        if "enable_hr" in model_pipeline and model_pipeline["enable_hr"] is not None:
            req_enable_hr = bool(model_pipeline["enable_hr"]) and not is_bg
            req_hr_scale = float(model_pipeline.get("hr_scale", 1.5)) if req_enable_hr else 1.0
        else:
            if arch_cfg.get("is_xl"):
                req_hr_scale = 1.0
                req_enable_hr = False
            else:
                req_hr_scale = float(model_pipeline.get("hr_scale", 1.5))
                req_enable_hr = True if not is_bg else False

        default_upscaler = "R-ESRGAN 4x+"
        hr_upscaler = model_pipeline.get("hires_upscaler") or model_pipeline.get("hr_upscaler") or default_upscaler
        denoising_strength = float(model_pipeline.get("hires_denoise", 0.35))
        configured_hr_steps = model_pipeline.get("hr_second_pass_steps")
        hr_second_pass_steps = resolve_hires_second_pass_steps(
            base_steps=steps,
            denoising_strength=denoising_strength,
            configured_steps=configured_hr_steps if (configured_hr_steps is not None and int(configured_hr_steps) > 0) else None,
        )

        # Адаптивный резолвер Hires.fix под реальный бюджет VRAM с диагностическим паспортом
        hires_res = self._resolve_hires_policy_for_plan(
            width=width,
            height=height,
            architecture=arch_cfg.get("architecture") or ("sdxl" if arch_cfg.get("is_xl") else "sd15"),
            checkpoint=target_ckpt,
            enable_adetailer=enable_ad,
            enable_hand_adetailer=hands_enabled,
            requested_enable_hr=req_enable_hr,
            requested_hr_scale=req_hr_scale,
            hr_upscaler=hr_upscaler,
            ad_inpaint_size=ad_cfg.get("face_inpaint_size", 512),
            active_lora_count=len(all_loras_in_prompt) if "all_loras_in_prompt" in locals() else 0,
        )

        enable_hr = bool(hires_res.get("allowed", False))
        hr_scale = float(hires_res.get("effective_scale", 1.0)) if enable_hr else 1.0

        custom_base_neg = (
            (model_pipeline.get("negative_prompt") if isinstance(model_pipeline, dict) else "")
            or (router_profile.get("negative_prompt") if isinstance(router_profile, dict) else "")
            or ""
        )
        custom_base_neg = str(custom_base_neg).strip() if (isinstance(custom_base_neg, str)) else ""

        arch_extra = arch_cfg.get("extra_negative", "")
        combined_user_neg = f"{arch_extra}, {user_neg_from_intent}".strip(" ,") if arch_extra else user_neg_from_intent

        final_negative = build_negative_prompt(
            final_positive,
            selected_profile.id,
            canon.base_negative,
            user_neg=combined_user_neg,
            archetype=archetype,
            custom_base_neg=custom_base_neg
        )
        final_negative = sanitize_prompt(final_negative)

        plan_negative = final_negative
        scene_type = getattr(intent, "scene_type", None)
        if not scene_type and isinstance(cast_data, dict):
            scene_type = cast_data.get("scene_type")
        if scene_type == "intimate":
            plan_negative = f"clothes, dress, armor, skirt, gloves, boots, fabric, {plan_negative}".strip(" ,")
        final_negative = plan_negative

        # Minor Safety Gate (Защита несовершеннолетних персонажей)
        from services.minor_safety import MinorSafetyDetector

        scene_has_minor = False
        if getattr(intent, "character_card", None) and getattr(intent.character_card, "is_minor", False):
            scene_has_minor = True
        elif character and getattr(character, "is_minor", False):
            scene_has_minor = True

        if not scene_has_minor and isinstance(cast_data, dict):
            for ent_name, ent_data in cast_data.get("entities", {}).items():
                if isinstance(ent_data, dict) and ent_data.get("is_minor"):
                    if has_secondary or ent_data.get("canonical_name", "").lower() == focus_name.lower():
                        scene_has_minor = True
                        break

        if scene_has_minor:
            location_dna = loc_dna_active or ""
            final_positive = MinorSafetyDetector.sanitize_positive_prompt(final_positive, context_hint=location_dna)
            final_negative = f"{MinorSafetyDetector.HARD_NEGATIVE_STACK}, {final_negative}".strip(", ")

        # Разрешение раздельных слотов ADetailer (Face и Hands)
        # 1. Слот лица
        if intent.face_prompt is not None:
            face_source = "json"
            if intent.face_prompt.strip() == "":
                resolved_face_prompt = "[SKIP]"
            else:
                face_clean = sanitize_prompt(intent.face_prompt)
                if is_elaria:
                    face_clean = f"{ELARIA_VISUAL_DNA}, {face_clean}".strip(" ,")
                elif not is_assistant:
                    active_comp_dna = companion_dna or getattr(intent, "companion_dna", None)
                    if active_comp_dna:
                        face_clean = f"{active_comp_dna}, {face_clean}".strip(" ,")
                if llm_guidance:
                    face_clean = _mix_guidance_tags(face_clean, llm_guidance, max_tags=6)
                if unique_loras:
                    resolved_face_prompt = f"{', '.join(unique_loras)}, {face_clean}".strip(" ,")
                else:
                    resolved_face_prompt = face_clean
        else:
            face_source = "fallback"
            resolved_face_prompt = final_positive

        # 2. Слот рук
        manual_hand_override = model_pipeline.get("adetailer_hands_prompt") if isinstance(model_pipeline, dict) else None
        if manual_hand_override and str(manual_hand_override).strip() and "legacy" not in str(manual_hand_override).lower():
            hands_source = "override"
            resolved_hands_prompt = str(manual_hand_override).strip()
        elif intent.hands_prompt is not None:
            hands_source = "json"
            if intent.hands_prompt.strip() == "":
                resolved_hands_prompt = "[SKIP]"
            else:
                hands_clean = sanitize_prompt(intent.hands_prompt)
                if llm_guidance:
                    hands_clean = _mix_guidance_tags(hands_clean, llm_guidance, max_tags=6)
                if unique_loras:
                    resolved_hands_prompt = f"{', '.join(unique_loras)}, {hands_clean}".strip(" ,")
                else:
                    resolved_hands_prompt = hands_clean
        else:
            hands_source = "fallback"
            resolved_hands_prompt = final_positive

        manual_hand_neg_override = model_pipeline.get("adetailer_hands_negative_prompt") if isinstance(model_pipeline, dict) else None
        if manual_hand_neg_override and str(manual_hand_neg_override).strip() and "legacy" not in str(manual_hand_neg_override).lower():
            resolved_hands_neg = str(manual_hand_neg_override).strip()
        elif intent.hands_prompt is not None and resolved_hands_prompt != "[SKIP]":
            hand_safety_tokens = [
                "bad hands", "mutated hands", "extra fingers", "fused fingers",
                "missing fingers", "poorly drawn hands", "malformed hands", "deformed fingers"
            ]
            fn_lower = final_negative.lower()
            missing_tokens = [tok for tok in hand_safety_tokens if tok not in fn_lower]
            if missing_tokens:
                resolved_hands_neg = f"{final_negative}, {', '.join(missing_tokens)}".strip(" ,")
            else:
                resolved_hands_neg = final_negative
        else:
            resolved_hands_neg = final_negative

        plan_model_id = router_profile.get("id", selected_profile.id) if router_profile else selected_profile.id
        plan_model_title = router_profile.get("name", selected_profile.title) if router_profile else selected_profile.title

        plan = GenerationPlan(
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
            denoising_strength=denoising_strength,
            hr_second_pass_steps=hr_second_pass_steps,
            clip_skip=clip_skip,
            enable_adetailer=enable_ad,
            enable_hand_adetailer=hands_enabled,
            ad_denoising_strength=ad_cfg["face_denoise"],
            ad_prompt=resolved_face_prompt,
            ad_negative_prompt=final_negative,
            ad_mask_blur=4,
            seed=effective_seed,
            hires_upscaler=hr_upscaler,
            hires_denoise=denoising_strength,
            reasoning=f"Стиль: {plan_model_title} ({plan_model_id})",
            style_badge=f"✨ {plan_model_title}",
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
            adetailer_hands_prompt=resolved_hands_prompt,
            adetailer_hands_negative_prompt=resolved_hands_neg,
            ad_source=face_source,
            adetailer_hands_source=hands_source,
            archetype=archetype,
        )

        # Формирование информативного блока ADetailer для лога
        ad_face_info, ad_hands_info = self._format_adetailer_log_blocks(plan)

        p_pos = getattr(plan, "positive_prompt", "")
        p_neg = getattr(plan, "negative_prompt", "")
        p_title = getattr(plan, "model_title", "")
        p_style = getattr(plan, "target_model_key", getattr(plan, "style", ""))
        p_ckpt = getattr(plan, "target_checkpoint", "")
        p_w = getattr(plan, "width", 512)
        p_h = getattr(plan, "height", 512)
        p_scale = getattr(plan, "hr_scale", 1.0)
        p_arch = getattr(plan, "archetype", "unknown")
        p_steps = getattr(plan, "steps", 20)
        p_cfg = getattr(plan, "cfg_scale", 7.0)
        p_denoise = getattr(plan, "denoising_strength", 0.35)
        p_denoise_str = f"{p_denoise}" if getattr(plan, "enable_hr", False) else "N/A"
        p_sampler = getattr(plan, "sampler_name", "DPM++ 2M")
        p_sched = getattr(plan, "scheduler", "Karras")
        p_clip = getattr(plan, "clip_skip", 1)
        p_seed = getattr(plan, "seed", -1)
        p_hr_steps = getattr(plan, "hr_second_pass_steps", hr_second_pass_steps)
        hires_diag = (
            f" (Hires.fix {p_scale}x -> {int(round(p_w * p_scale))}x{int(round(p_h * p_scale))}, steps={p_hr_steps}, denoise={p_denoise})"
            if getattr(plan, "enable_hr", False)
            else f" (Hires.fix {p_scale}x -> {int(round(p_w * p_scale))}x{int(round(p_h * p_scale))})"
        )

        logger.info(
            f"\n================ [ART DIRECTOR: PLAN FROM VISUAL INTENT] ================\n"
            f"[PROMPT CLEAN]    : \"{p_pos}\"\n"
            f"[NEGATIVE]        : \"{p_neg}\"\n"
            f"[PROFILE / CKPT]  : {p_title} ({p_style}) -> {p_ckpt}\n"
            f"[RESOLUTION]      : {p_w}x{p_h}{hires_diag} | Archetype={p_arch}\n"
            f"[PARAMS]          : Steps={p_steps} | CFG={p_cfg} | Denoise={p_denoise_str} | "
            f"Sampler={p_sampler} | Scheduler={p_sched} | ClipSkip={p_clip} | Seed={p_seed}\n"
            f"[ADETAILER FACE]  : {ad_face_info}\n"
            f"[ADETAILER HANDS] : {ad_hands_info}\n"
            f"========================================================================="
        )
        self._log_vram_planner_passport(
            plan=plan,
            architecture=arch_cfg.get("architecture") or ("sdxl" if _is_xl_v else "sd15"),
            lora_count=len(all_loras_in_prompt),
        )
        return plan

    def plan_generation(
        self,
        raw_prompt: str,
        preferred_style: Optional[str] = None,
        lm_client: Optional[Any] = None,
        character: Optional[Any] = None,
        available_models: Optional[List[Dict[str, Any]]] = None,
        seed: Optional[int] = None,
        character_name: Optional[str] = None,
        session_id: Optional[str] = None,
        mode: Optional[str] = None,
        companion_dna: Optional[str] = None,
        is_scene_background: bool = False
    ) -> GenerationPlan:
        """
        Быстрый чистый метод на CPU:
        - Принимает raw_prompt (английский текст из тега [IMAGE_PROMPT:]), preferred_style, available_models, seed.
        - Если передан --neg <текст>, отделяет пользовательский негатив.
        - Определяет профиль: если передан preferred_style — берет его из MODEL_REGISTRY; иначе выбирает через model_routing_rules.json.
        - Определяет семантический архетип кадра и нативное разрешение.
        - Проверяет наличие человека в кадре. Если человек есть и профиль поддерживает ADetailer (canon.allow_adetailer), включает enable_adetailer=True.
        - Устанавливает Hires.fix с защитой SDXL от OOM (SD 1.5: 1.5x, SDXL: отключен).
        - Возвращает полностью готовый объект GenerationPlan.
        """
        effective_seed = seed if (seed is not None and seed != -1) else self.next_seed
        self.next_seed = -1  # Сброс после использования

        # 1. Отделение пользовательского негатива (--neg <текст>)
        user_neg = ""
        clean_prompt = (raw_prompt or "").strip()
        neg_match = re.search(r'-{1,2}(?:negative|neg)\s+(.*?)(?=(?:--model|--style|--ar|--hands|$))', clean_prompt, re.IGNORECASE)
        if neg_match:
            user_neg = neg_match.group(1).strip()
            clean_prompt = clean_prompt[:neg_match.start()] + clean_prompt[neg_match.end():]
            clean_prompt = clean_prompt.strip(' ,')
        else:
            has_neg, p_pos, p_neg = parse_user_negative_trigger(clean_prompt)
            if has_neg:
                clean_prompt = p_pos.strip(' ,')
                user_neg = p_neg.strip(' ,')
        clean_neg = user_neg

        # 1.1 Отделение флага соотношения сторон (--ar)
        ar_override = None
        ar_match = re.search(r'--ar[:=\s]+([a-zA-Z0-9:]+)', clean_prompt, re.IGNORECASE)
        if ar_match:
            ar_override = ar_match.group(1).lower().strip()
            clean_prompt = clean_prompt[:ar_match.start()] + clean_prompt[ar_match.end():]
            clean_prompt = re.sub(r'\s{2,}', ' ', clean_prompt).strip(' ,')

        clean_prompt = re.sub(r'--ar[:=\s]+[a-zA-Z0-9:]+', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')

        # Отделение флага детальной прорисовки рук (--hands) и автопортрета (--self / --subject)
        wants_hands = bool(re.search(r'--hands\b', clean_prompt, re.IGNORECASE))
        clean_prompt = re.sub(r'--hands\b', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')
        has_self_flag = bool(re.search(r'(?:^|[\s,])--self\b', clean_prompt, re.IGNORECASE))
        subj_match = re.search(r'--subject[:=\s]+["\']?([a-zA-Z0-9_\-]+)["\']?', clean_prompt, re.IGNORECASE)
        if subj_match and subj_match.group(1).strip().lower() in ("elaria", "self", "assistant"):
            has_self_flag = True
        clean_prompt = re.sub(r'(?:^|[\s,])--self\b', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')
        clean_prompt = re.sub(r'--subject[:=\s]+["\']?[a-zA-Z0-9_\-]+["\']?', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')
        clean_prompt = re.sub(r'(?:--model|--style)\s+[a-zA-Z0-9_\-]+', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')

        # Защита LoRA-тегов от агрессивной санитизации
        explicit_loras = []
        lora_pattern = re.compile(r'<lora:[^>]+>')
        for match in lora_pattern.finditer(clean_prompt):
            explicit_loras.append(match.group(0))
        clean_prompt = lora_pattern.sub('', clean_prompt).strip(' ,')

        clean_prompt = re.sub(r'[\(\[]\s*lora:[a-zA-Z0-9_\-.]+(?::[0-9.]+)?\s*[\)\]]', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')
        clean_prompt = re.sub(r'(?<!-)\b(?:v[0-9]+(?:\.[0-9]+)?|final|pruned|fp16)\b$', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')

        clean_prompt = sanitize_prompt(clean_prompt)
        clean_prompt = tagify_prompt_with_lm(clean_prompt, lm_client=lm_client)
        clean_prompt = _clean_context_subtag(str(clean_prompt))

        # Возвращаем LoRA строго в начало промпта
        if explicit_loras:
            clean_prompt = f"{', '.join(explicit_loras)}, {clean_prompt}".strip(" ,")
        clean_prompt, auto_neg_extracted = extract_negations_from_prompt(clean_prompt)
        if auto_neg_extracted:
            extra_auto = ", ".join(auto_neg_extracted)
            clean_neg = f"{extra_auto}, {clean_neg}".strip(" ,") if clean_neg else extra_auto
        if clean_neg:
            clean_neg = _clean_context_subtag(str(clean_neg))

        # 2. Определение профиля модели
        selected_profile: Optional[ModelProfile] = None

        if preferred_style and not preferred_style.startswith("✨") and preferred_style.lower() not in ("default", "none", "auto"):
            pref_key = preferred_style.lower()
            if pref_key in MODEL_REGISTRY:
                selected_profile = MODEL_REGISTRY[pref_key]
            else:
                style_id = detect_style_from_checkpoint(preferred_style)
                selected_profile = MODEL_REGISTRY.get(style_id)

        router_profile_pref = None
        if preferred_style and hasattr(self, "router") and self.router and hasattr(self.router, "profiles"):
            ps_clean = str(preferred_style).strip().lower()
            for p in self.router.profiles:
                p_name = str(p.get("name", "")).strip().lower()
                p_id = str(p.get("id", "")).strip().lower()
                if ps_clean and (ps_clean == p_name or ps_clean == p_id):
                    router_profile_pref = p
                    break

        actual_model_ckpt = router_profile_pref.get("checkpoint") if router_profile_pref else None
        if not actual_model_ckpt and preferred_style and hasattr(self, "router") and self.router:
            actual_model_ckpt = self.router.get_checkpoint_for_model(preferred_style)

        best_model_id = None
        if not selected_profile:
            best_model_id, score = self.router.match_prompt(clean_prompt)
            logger.info(f"[ArtDirector] Vector routing match: prompt='{clean_prompt}' -> winner='{best_model_id}' (score: {score:.4f})")

            actual_model_ckpt = self.router.get_checkpoint_for_model(best_model_id)

            if best_model_id in MODEL_REGISTRY:
                selected_profile = MODEL_REGISTRY[best_model_id]
            else:
                ckpt = actual_model_ckpt or best_model_id
                style_id = detect_style_from_checkpoint(ckpt)
                selected_profile = MODEL_REGISTRY.get(style_id) or MODEL_REGISTRY.get("realistic", list(MODEL_REGISTRY.values())[0])

        canon = selected_profile.canon

        # 3. Выбор целевого чекпоинта
        if actual_model_ckpt:
            target_ckpt = actual_model_ckpt
        elif preferred_style and preferred_style.endswith(".safetensors"):
            target_ckpt = preferred_style
        else:
            target_ckpt = selected_profile.canonical_filename
            if available_models:
                matched = find_checkpoint_for_style(selected_profile.id, available_models)
                if matched:
                    target_ckpt = matched

        router_profile = self.router.get_profile(preferred_style or best_model_id or actual_model_ckpt or selected_profile.id) if hasattr(self, "router") and self.router else None
        if not router_profile and target_ckpt and hasattr(self, "router") and self.router:
            router_profile = self.router.get_profile(target_ckpt)
        if not router_profile and hasattr(self, "router") and self.router and hasattr(self.router, "profiles"):
            target_ids = {selected_profile.id, preferred_style, best_model_id}
            target_names = {str(preferred_style).lower(), str(best_model_id).lower(), str(getattr(selected_profile, "title", "")).lower()}
            for p in self.router.profiles:
                if (p.get("id") in target_ids) or (target_ckpt and p.get("checkpoint") == target_ckpt):
                    router_profile = p
                    break
                p_name = p.get("name", "").lower()
                if p_name and p_name in target_names:
                    router_profile = p
                    break
                if target_ckpt and p.get("checkpoint"):
                    t_stem = Path(target_ckpt).stem.lower().replace("final", "").replace("_v5", "").replace("v5", "")
                    p_stem = Path(p["checkpoint"]).stem.lower().replace("final", "").replace("_v5", "").replace("v5", "")
                    if t_stem and p_stem and (t_stem in p_stem or p_stem in t_stem):
                        router_profile = p
                        break

        # 4. Формирование позитивного промпта с учетом режима
        eff_mode = mode or ("roleplay" if (character is not None or (character_name and character_name.lower() not in ("элария", "elaria"))) else "assistant")
        is_assistant = (eff_mode == "assistant")
        has_female_markers = bool(re.search(r'\b(1girl|woman|girl|female|lady|maid|queen|princess|model|face|portrait)\b', clean_prompt, re.I))
        has_elaria_name = bool(re.search(r'\b(elaria|элария)\b', clean_prompt, re.I))
        is_elaria = is_assistant and (has_self_flag or has_elaria_name or (character_name and character_name.lower() in ("элария", "elaria") and has_female_markers))
        archetype = detect_scene_archetype(clean_prompt)

        focus_name = ""
        secondary_companion_name = ""
        has_secondary = False
        cast_data = {}
        loc_dna_active = ""

        if is_assistant:
            if is_elaria and archetype != "scenery":
                clean_prompt = re.sub(r'\b(elaria|элария)\b', '', clean_prompt, flags=re.I).strip(' ,')
                clean_prompt = f"{ELARIA_VISUAL_DNA}, {clean_prompt}".strip(' ,')
        else:
            is_closeup_frame = (archetype == "closeup" or (ar_override and str(ar_override).lower() in ("closeup", "square", "1:1", "sq")))
            rp_res = _assemble_roleplay_context(
                base_prompt=clean_prompt,
                session_id=session_id,
                character_name=character_name,
                character=character,
                intent_companion_dna=companion_dna,
                intent_companion_outfit=None,
                intent_location_dna=None,
                intent_mutations=None,
                is_closeup_frame=is_closeup_frame
            )
            clean_prompt = rp_res["prompt"]
            focus_name = rp_res["focus_name"]
            secondary_companion_name = rp_res["secondary_name"]
            has_secondary = rp_res["has_secondary"]
            loc_dna_active = rp_res["loc_dna"]
            cast_data = rp_res["cast_data"]
            archetype = detect_scene_archetype(clean_prompt)

        # Подмешивание тегов llm_guidance из профиля модели к full_positive (первые 6-8 тегов)
        full_positive = clean_prompt
        llm_guidance = ""
        if router_profile and isinstance(router_profile, dict):
            llm_guidance = router_profile.get("llm_guidance") or ""
        if not llm_guidance and selected_profile:
            if isinstance(selected_profile, dict):
                llm_guidance = selected_profile.get("llm_guidance") or ""
            else:
                llm_guidance = getattr(selected_profile, "llm_guidance", "") or ""

        if llm_guidance:
            full_positive = _mix_guidance_tags(full_positive, llm_guidance, max_tags=8)
        clean_prompt = full_positive

        # Резолвинг LoRA (персонажи + micro-detail)
        effective_char_name = character_name
        if not effective_char_name and character:
            effective_char_name = getattr(character, "name", None) if not isinstance(character, str) else character

        char_names_for_lora: List[str] = []
        if is_assistant:
            if is_elaria:
                char_names_for_lora.append("Элария")
        else:
            if effective_char_name:
                char_names_for_lora.append(str(effective_char_name))
            if focus_name and focus_name not in char_names_for_lora:
                char_names_for_lora.append(focus_name)
            if secondary_companion_name and secondary_companion_name not in char_names_for_lora:
                char_names_for_lora.append(secondary_companion_name)

        lora_char_arg = ", ".join(char_names_for_lora) if char_names_for_lora else None

        lora_tags, act_tags = LoraManager.resolve_loras(
            prompt=clean_prompt,
            character_name=lora_char_arg,
            style_id=selected_profile.id,
            target_model_name=target_ckpt
        )

        final_tokens = [clean_prompt]
        if act_tags:
            final_tokens.insert(0, ", ".join(act_tags))
        if lora_tags:
            final_tokens.extend(lora_tags)
        final_positive = ", ".join(t.strip(" ,") for t in final_tokens if t.strip(" ,"))

        # Отделяем LoRA от описательного тела перед санитизацией
        all_loras_in_prompt = lora_pattern.findall(final_positive)
        clean_body_no_lora = lora_pattern.sub('', final_positive).strip(' ,')

        clean_prompt = clean_body_no_lora
        clean_prompt = re.sub(r'(?<!<)\b(?:\(?\[?)lora:[a-zA-Z0-9_\-.]+(?::[0-9.]+)?(?:\)?\]?)(?!>)', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')
        clean_body_no_lora = re.sub(r'[\(\[]\s*lora:[a-zA-Z0-9_\-.]+(?::[0-9.]+)?\s*[\)\]]', '', clean_prompt, flags=re.IGNORECASE).strip(' ,')
        clean_body_no_lora = re.sub(r'(?<!-)\b(?:v[0-9]+(?:\.[0-9]+)?|final|pruned|fp16)\b$', '', clean_body_no_lora, flags=re.IGNORECASE).strip(' ,')

        prefix = canon.prompt_prefix.strip(' ,') if canon.prompt_prefix else ""
        if prefix and prefix.lower() not in clean_body_no_lora.lower():
            assembled_body = f"{prefix}, {clean_body_no_lora}".strip(' ,')
        else:
            assembled_body = clean_body_no_lora

        assembled_body = sanitize_prompt(assembled_body)
        clean_prompt = assembled_body

        # LoRA гарантированно на 1-й позиции
        if all_loras_in_prompt:
            seen_loras = set()
            unique_loras = []
            for l_tag in all_loras_in_prompt:
                l_key = l_tag.lower().replace(" ", "")
                if l_key not in seen_loras:
                    seen_loras.add(l_key)
                    unique_loras.append(l_tag)
            final_positive = f"{', '.join(unique_loras)}, {assembled_body}".strip(' ,')
        else:
            unique_loras = []
            final_positive = assembled_body

        # 5. Разрешение геометрии: прямое подчинение флагу --ar
        ar_clean = str(ar_override).strip().lower() if ar_override else None
        if ar_clean in ("portrait", "vertical", "tall", "2:3"):
            archetype = "solo"
        elif ar_clean in ("wide", "landscape", "horizontal", "3:2"):
            archetype = "pair"
        elif ar_clean in ("square", "sq", "1:1", "closeup"):
            archetype = "closeup"
        elif ar_clean in ("fullbody", "9:16"):
            archetype = "fullbody"
        elif ar_clean in ("cinematic", "pano", "16:9"):
            archetype = "cinematic"
        elif ar_clean in ("ultrawide", "wide21:9", "21:9", "anamorphic"):
            archetype = "ultrawide"
        elif ar_clean == "scenery":
            archetype = "scenery"
        elif ar_clean in ASPECT_RATIO_BUCKETS:
            archetype = ar_clean
        else:
            # Если явного ar_override нет:
            # При наличии secondary_dna или 2girls и маркеров бокового расположения (side by side, duo, BREAK) -> archetype = "pair".
            fp_low = final_positive.lower()
            is_pair_candidate = bool(has_secondary or "2girls" in fp_low)
            pair_layout_markers = ("side by side", "duo", " break ", "together", "couple")
            if is_pair_candidate and any(m in fp_low for m in pair_layout_markers):
                archetype = "pair"
            else:
                detected = detect_scene_archetype(final_positive)
                archetype = archetype if archetype != "solo" else (detected or "solo")

        # 6. Конфигурация пайплайна модели
        model_pipeline = router_profile.get("pipeline", {}) if router_profile else {}
        if not model_pipeline and hasattr(self, "router") and self.router and hasattr(self.router, "profiles"):
            target_model_id = selected_profile.id
            target_checkpoint = target_ckpt
            for m in self.router.profiles:
                if m.get("id") == target_model_id or m.get("checkpoint") == target_checkpoint:
                    model_pipeline = m.get("pipeline", {})
                    break

        if router_profile and isinstance(router_profile, dict):
            model_target = dict(router_profile)
            if target_ckpt:
                if not model_target.get("checkpoint") or target_ckpt != model_target.get("checkpoint"):
                    if is_xl_model(target_ckpt):
                        model_target["architecture"] = "sdxl"
                        model_target["checkpoint"] = target_ckpt
                    elif not is_xl_model(target_ckpt) and model_target.get("architecture") == "sdxl":
                        model_target["architecture"] = "sd15"
                        model_target["checkpoint"] = target_ckpt
                    else:
                        model_target["checkpoint"] = target_ckpt
        else:
            model_target = router_profile or f"{target_ckpt} {selected_profile.id} {selected_profile.title}"
        arch_cfg = get_archetype_config(model_target, archetype)
        width = arch_cfg["width"]
        height = arch_cfg["height"]

        # 7. Сборка негатива
        custom_neg_from_profile = ""
        if router_profile:
            custom_neg_from_profile = (
                (router_profile.get("pipeline", {}) or {}).get("negative_prompt")
                or router_profile.get("negative_prompt")
                or ""
            ).strip()

        arch_extra = arch_cfg.get("extra_negative", "")
        if arch_extra and arch_extra not in clean_neg:
            clean_neg = f"{arch_extra}, {clean_neg}" if clean_neg else arch_extra

        final_negative = build_negative_prompt(
            final_positive,
            selected_profile.id,
            canon.base_negative,
            user_neg=clean_neg,
            archetype=archetype,
            custom_base_neg=custom_neg_from_profile
        )
        final_negative = sanitize_prompt(final_negative)

        plan_negative = final_negative
        scene_type = cast_data.get("scene_type") if isinstance(cast_data, dict) else None
        if scene_type == "intimate":
            plan_negative = f"clothes, dress, armor, skirt, gloves, boots, fabric, {plan_negative}".strip(" ,")
        final_negative = plan_negative

        is_bg = bool(is_scene_background)
        plan_model_id = router_profile.get("id", selected_profile.id) if router_profile else selected_profile.id
        plan_model_title = router_profile.get("name", selected_profile.title) if router_profile else selected_profile.title
        _is_xl_p = ("xl" in str(plan_model_id).lower() or "xl" in str(target_ckpt).lower() or is_sdxl_checkpoint(target_ckpt))
        _is_xl_v = _is_xl_p

        # Единый резолвинг ADetailer (SSOT до расчёта Hires.fix и сборки плана)
        ad_cfg = self._resolve_adetailer_pipeline(
            model_pipeline=model_pipeline,
            canon=canon,
            is_bg=is_bg,
            is_xl=_is_xl_p,
            wants_hands=bool(wants_hands),
        )
        enable_ad = ad_cfg["enable_adetailer"]
        hands_enabled = ad_cfg["hands_enabled"]

        if "enable_hr" in model_pipeline and model_pipeline["enable_hr"] is not None:
            req_enable_hr = bool(model_pipeline["enable_hr"]) and not is_bg
            req_hr_scale = float(model_pipeline.get("hr_scale", 1.5)) if req_enable_hr else 1.0
        else:
            if arch_cfg.get("is_xl"):
                req_hr_scale = 1.0
                req_enable_hr = False
            else:
                req_hr_scale = float(model_pipeline.get("hr_scale", 1.5))
                req_enable_hr = True if not is_bg else False

        default_upscaler = "R-ESRGAN 4x+"
        hr_upscaler = model_pipeline.get("hires_upscaler") or model_pipeline.get("hr_upscaler") or default_upscaler
        hr_denoise = float(model_pipeline.get("hires_denoise", 0.35))
        configured_hr_steps = model_pipeline.get("hr_second_pass_steps")
        hr_second_pass_steps = resolve_hires_second_pass_steps(
            base_steps=int(model_pipeline.get("steps", canon.recommended_steps)),
            denoising_strength=hr_denoise,
            configured_steps=configured_hr_steps if (configured_hr_steps is not None and int(configured_hr_steps) > 0) else None,
        )

        # Адаптивный резолвер Hires.fix под реальный бюджет VRAM с диагностическим паспортом
        hires_res = self._resolve_hires_policy_for_plan(
            width=width,
            height=height,
            architecture=arch_cfg.get("architecture") or ("sdxl" if arch_cfg.get("is_xl") else "sd15"),
            checkpoint=target_ckpt,
            enable_adetailer=enable_ad,
            enable_hand_adetailer=hands_enabled,
            requested_enable_hr=req_enable_hr,
            requested_hr_scale=req_hr_scale,
            hr_upscaler=hr_upscaler,
            ad_inpaint_size=ad_cfg.get("face_inpaint_size", 512),
            active_lora_count=len(all_loras_in_prompt) if "all_loras_in_prompt" in locals() else 0,
        )

        enable_hr = bool(hires_res.get("allowed", False))
        hr_scale = float(hires_res.get("effective_scale", 1.0)) if enable_hr else 1.0

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

        # 8. Сборка GenerationPlan (единый источник параметров ADetailer из ad_cfg)
        plan = GenerationPlan(
            style=plan_model_id,
            target_model_key=plan_model_id,
            model_title=plan_model_title,
            target_checkpoint=target_ckpt,
            positive_prompt=final_positive,
            negative_prompt=final_negative,
            width=width,
            height=height,
            steps=int(model_pipeline.get("steps", canon.recommended_steps)),
            cfg_scale=float(model_pipeline.get("cfg_scale", canon.default_cfg)),
            sampler_name=sampler_name,
            scheduler=scheduler,
            enable_hr=enable_hr,
            hr_scale=hr_scale,
            hr_upscaler=hr_upscaler,
            denoising_strength=hr_denoise,
            hr_second_pass_steps=hr_second_pass_steps,
            clip_skip=int(model_pipeline.get("clip_skip", canon.clip_skip)),
            enable_adetailer=enable_ad,
            enable_hand_adetailer=hands_enabled,
            ad_denoising_strength=ad_cfg["face_denoise"],
            ad_prompt=final_positive,
            ad_negative_prompt=final_negative,
            ad_mask_blur=4,
            seed=effective_seed,
            hires_upscaler=hr_upscaler,
            hires_denoise=hr_denoise,
            reasoning=f"Стиль: {plan_model_title} ({plan_model_id})",
            style_badge=f"✨ {plan_model_title}",
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
            adetailer_hands_prompt=final_positive,
            adetailer_hands_negative_prompt=final_negative,
            ad_source="fallback",
            adetailer_hands_source="fallback",
            archetype=archetype,
            raw_prompt=raw_prompt,
        )

        # Формирование информативного блока ADetailer для лога
        ad_face_info, ad_hands_info = self._format_adetailer_log_blocks(plan)

        p_raw = getattr(plan, "raw_prompt", raw_prompt)
        p_pos = getattr(plan, "positive_prompt", "")
        p_neg = getattr(plan, "negative_prompt", "")
        p_title = getattr(plan, "model_title", "")
        p_style = getattr(plan, "target_model_key", getattr(plan, "style", ""))
        p_ckpt = getattr(plan, "target_checkpoint", "")
        p_w = getattr(plan, "width", 512)
        p_h = getattr(plan, "height", 512)
        p_scale = getattr(plan, "hr_scale", 1.0)
        p_arch = getattr(plan, "archetype", "unknown")
        p_steps = getattr(plan, "steps", 20)
        p_cfg = getattr(plan, "cfg_scale", 7.0)
        p_denoise = getattr(plan, "denoising_strength", 0.35)
        p_denoise_str = f"{p_denoise}" if getattr(plan, "enable_hr", False) else "N/A"
        p_sampler = getattr(plan, "sampler_name", "DPM++ 2M")
        p_sched = getattr(plan, "scheduler", "Karras")
        p_clip = getattr(plan, "clip_skip", 1)
        p_seed = getattr(plan, "seed", -1)
        p_hr_steps = getattr(plan, "hr_second_pass_steps", hr_second_pass_steps)
        hires_diag = (
            f" (Hires.fix {p_scale}x -> {int(round(p_w * p_scale))}x{int(round(p_h * p_scale))}, steps={p_hr_steps}, denoise={p_denoise})"
            if getattr(plan, "enable_hr", False)
            else f" (Hires.fix {p_scale}x -> {int(round(p_w * p_scale))}x{int(round(p_h * p_scale))})"
        )

        logger.info(
            f"\n======================= [ART DIRECTOR: PLAN GENERATION] =======================\n"
            f"[INPUT RAW]       : \"{p_raw}\"\n"
            f"[PROMPT CLEAN]    : \"{p_pos}\"\n"
            f"[NEGATIVE]        : \"{p_neg}\"\n"
            f"[PROFILE / CKPT]  : {p_title} ({p_style}) -> {p_ckpt}\n"
            f"[RESOLUTION]      : {p_w}x{p_h}{hires_diag} | Archetype={p_arch}\n"
            f"[PARAMS]          : Steps={p_steps} | CFG={p_cfg} | Denoise={p_denoise_str} | "
            f"Sampler={p_sampler} | Scheduler={p_sched} | ClipSkip={p_clip} | Seed={p_seed}\n"
            f"[ADETAILER FACE]  : {ad_face_info}\n"
            f"[ADETAILER HANDS] : {ad_hands_info}\n"
            f"================================================================================"
        )
        self._log_vram_planner_passport(
            plan=plan,
            architecture=arch_cfg.get("architecture") or ("sdxl" if _is_xl_p else "sd15"),
            lora_count=len(all_loras_in_prompt) if "all_loras_in_prompt" in locals() else 0,
        )

        return plan

    generate_plan = plan_generation

    def build_direct_plan(
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
        clean_raw = re.sub(r'^(?:!art|!арт|!img|!картинка)\s*', '', raw_prompt, flags=re.IGNORECASE).strip()
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

        default_w, default_h = (1024, 1024) if is_xl else (512, 768)
        width = int(user_params.get("width", default_w))
        height = int(user_params.get("height", default_h))

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

        if "enable_hr" in user_params:
            req_enable_hr = bool(user_params["enable_hr"])
            req_hr_scale = float(user_params.get("hr_scale", model_pipeline.get("hr_scale", 1.5))) if req_enable_hr else 1.0
        elif "enable_hr" in model_pipeline and model_pipeline["enable_hr"] is not None:
            req_enable_hr = bool(model_pipeline["enable_hr"])
            req_hr_scale = float(model_pipeline.get("hr_scale", 1.5)) if req_enable_hr else 1.0
        elif is_xl:
            req_enable_hr = False
            req_hr_scale = 1.0
        else:
            req_enable_hr = True
            req_hr_scale = float(user_params.get("hr_scale", model_pipeline.get("hr_scale", 1.5)))

        hr_denoise = float(user_params.get("denoising_strength", model_pipeline.get("hires_denoise", 0.35)))
        default_upscaler = "R-ESRGAN 4x+"
        hr_upscaler = user_params.get("hr_upscaler") or model_pipeline.get("hires_upscaler") or model_pipeline.get("hr_upscaler") or default_upscaler
        raw_hr_steps = user_params.get("hr_second_pass_steps") if user_params.get("hr_second_pass_steps") is not None else model_pipeline.get("hr_second_pass_steps")
        hr_second_pass_steps = resolve_hires_second_pass_steps(
            base_steps=steps,
            denoising_strength=hr_denoise,
            configured_steps=raw_hr_steps if (raw_hr_steps is not None and int(raw_hr_steps) > 0) else None,
        )

        # Адаптивный резолвер Hires.fix под реальный бюджет VRAM с диагностическим паспортом
        hires_res = self._resolve_hires_policy_for_plan(
            width=width,
            height=height,
            architecture="sdxl" if is_xl else "sd15",
            checkpoint=target_ckpt,
            enable_adetailer=enable_ad,
            enable_hand_adetailer=hands_enabled,
            requested_enable_hr=req_enable_hr,
            requested_hr_scale=req_hr_scale,
            hr_upscaler=hr_upscaler,
            ad_inpaint_size=ad_cfg.get("face_inpaint_size", 512),
            active_lora_count=len(re.findall(r'<lora:[^>]+>', pos_raw)),
        )

        enable_hr = bool(hires_res.get("allowed", False))
        hr_scale = float(hires_res.get("effective_scale", 1.0)) if enable_hr else 1.0

        hires_diag = (
            f" (Hires.fix {hr_scale}x -> {int(round(width * hr_scale))}x{int(round(height * hr_scale))}, steps={hr_second_pass_steps}, denoise={hr_denoise})"
            if enable_hr else f" (Hires.fix {hr_scale}x -> {int(round(width * hr_scale))}x{int(round(height * hr_scale))})"
        )

        logger.info(
            f"\n"
            f"======================= [DIRECT ART: PURE DISPATCH] =======================\n"
            f"[POSITIVE]  : \"{final_positive}\"\n"
            f"[NEGATIVE]  : \"{final_negative}\"\n"
            f"[CKPT]      : {target_ckpt} ({plan_model_id})\n"
            f"[RESOLUTION]: {width}x{height}{hires_diag}\n"
            f"[INFERENCE] : Steps={steps} | CFG={cfg_scale} | Sampler={sampler_name}/{scheduler} | ClipSkip={clip_skip} | Seed={effective_seed}\n"
            f"[HIRES.FIX] : Enable={enable_hr} | Scale={hr_scale}x | Steps={hr_second_pass_steps} | Denoise={hr_denoise} | Upscaler={hr_upscaler}\n"
            f"[ADETAILER] : Face={'ON' if enable_ad else 'OFF'} | Hands={'ON' if hands_enabled else 'OFF'}\n"
            f"==========================================================================="
        )

        plan = GenerationPlan(
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
        )
        self._log_vram_planner_passport(
            plan=plan,
            architecture="sdxl" if is_xl else "sd15",
            lora_count=len(re.findall(r'<lora:[^>]+>', pos_raw)),
        )
        return plan
    def build_scene_background_plan(
        self,
        location_prompt: str,
        preferred_style: Optional[str] = "realistic",
        available_models: Optional[List[Dict[str, Any]]] = None,
        session_id: Optional[str] = None,
        lm_client: Optional[Any] = None,
        use_visual_intent: bool = False,
    ) -> GenerationPlan:
        """
        Единый SSOT-строитель плана генерации чистого фона сцены (768x512, без людей и без ADetailer).
        Используется как десктопным RoleplayControllerMixin, так и мобильным шлюзом.
        """
        clean_loc = (location_prompt or "").strip() or "cyberpunk city street at night, neon reflections, rain"
        human_neg = "human, humans, person, 1girl, 1boy, woman, man, character, people, crowd, face, hands, body, portrait, anime girl"

        if use_visual_intent:
            bg_pos = (
                f"masterpiece, best quality, scenic, {clean_loc}, "
                "empty landscape, architecture, atmospheric lighting, detailed background"
            )
            intent = VisualIntent(
                prompt=bg_pos,
                raw_user_query=clean_loc,
                user_neg=human_neg,
                aspect_ratio="landscape",
                session_id=session_id,
                mode="roleplay",
                is_scene_background=True,
            )
            plan = self.plan_from_visual_intent(
                visual_intent=intent,
                preferred_style=preferred_style,
                available_models=available_models,
                session_id=session_id,
                mode="roleplay",
            )
        else:
            plan = self.plan_generation(
                raw_prompt=clean_loc,
                preferred_style=preferred_style,
                lm_client=lm_client,
                character=None,
                available_models=available_models,
                session_id=session_id,
                mode="roleplay",
                is_scene_background=True,
            )
            plan.positive_prompt = f"scenery, empty landscape, atmospheric lighting, no humans, {plan.positive_prompt}".strip(" ,")
            plan.negative_prompt = f"{human_neg}, {plan.negative_prompt}".strip(" ,") if plan.negative_prompt else human_neg

        plan.width = 768
        plan.height = 512
        plan.enable_adetailer = False
        plan.enable_hand_adetailer = False
        plan.adetailer_hands_enabled = False
        plan.adetailer_hands = False
        return plan
