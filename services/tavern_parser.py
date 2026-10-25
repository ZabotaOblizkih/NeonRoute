"""
Tavern / Chub Character Card Parser for NeonRoute
Supports TavernCard V1, TavernCard V2 (chara_card_v2), and Chub.ai JSON card formats.
Extracts persona data, greetings, examples, and formats roleplay system prompts.
"""

import json
import re
import uuid
import base64
import logging
from dataclasses import dataclass, field, asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Union, List
from config import CHARACTERS_DIR

logger = logging.getLogger("NeonRoute.TavernParser")



# Facade Re-export (Zero-Breaking-Changes)
from services.tavern_models import LoreEntry, CharacterCard

__all__ = [
    "TavernParser",
    "LoreEntry",
    "CharacterCard",
]


class TavernParser:
    """Parses Tavern and Chub.ai character JSON cards."""

    @staticmethod
    def extract_lore_entries(data: dict) -> list[LoreEntry]:
        """
        Унифицированно извлекает статьи из character_book.entries, world_info.entries или корневого entries
        (поддерживая как list, так и dict.values()). Фильтрует пустые записи, заполняет keys, content, comment, enabled, constant.
        """
        if not isinstance(data, dict):
            return []

        target = data
        if isinstance(target.get("data"), dict):
            target = target["data"]
        if isinstance(target.get("character"), dict):
            target = target["character"]
        elif isinstance(target.get("definition"), dict):
            target = target["definition"]

        candidate_dicts = [target]
        if isinstance(data.get("data"), dict):
            candidate_dicts.append(data["data"])
        if isinstance(data.get("character"), dict):
            candidate_dicts.append(data["character"])
        if isinstance(data.get("definition"), dict):
            candidate_dicts.append(data["definition"])
        candidate_dicts.append(data)

        cb = None
        for d in candidate_dicts:
            if not isinstance(d, dict):
                continue
            for k in ("character_book", "lorebook", "world_info"):
                container = d.get(k)
                if isinstance(container, dict) and "entries" in container:
                    cb = container
                    break
            if cb:
                break
            if "entries" in d and isinstance(d["entries"], (dict, list)):
                cb = d
                break

        if not cb:
            return []

        raw_entries = cb.get("entries")
        if not raw_entries:
            return []

        if isinstance(raw_entries, dict):
            items = list(raw_entries.values())
        elif isinstance(raw_entries, list):
            items = raw_entries
        else:
            return []

        entries: list[LoreEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            raw_keys = item.get("keys") or item.get("key") or []
            if isinstance(raw_keys, list):
                keys = [str(k).strip() for k in raw_keys if str(k).strip()]
            elif isinstance(raw_keys, str):
                keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
            else:
                keys = []

            content = str(item.get("content") or item.get("text") or "").strip()
            comment = str(item.get("comment") or item.get("name") or "").strip()
            enabled = bool(item.get("enabled", True))
            constant = bool(item.get("constant", False) or item.get("always_active", False))

            if not content:
                continue

            entries.append(LoreEntry(
                keys=keys,
                content=content,
                comment=comment,
                enabled=enabled,
                constant=constant,
                raw=item
            ))

        return entries

    @staticmethod
    def extract_physical_dna_regex(text: str) -> str:
        if not text:
            return ""
        patterns = [
            r'\[(?:Appearance|Face|Eyes|Hair|Body|Build)\s*[:=]\s*([^\]]+)\]',
            r'(?:Appearance|Looks)\s*\(([^)]+)\)'
        ]
        clothing_stop = {"armor", "plate", "dress", "cloak", "robe", "boots", "skirt", "suit", "jacket", "доспех", "броня", "платье", "плащ"}
        tokens = []
        for p in patterns:
            for m in re.finditer(p, text, re.IGNORECASE):
                for t in m.group(1).replace('"', '').replace("'", "").split(','):
                    c = t.strip()
                    if c and not any(s in c.lower() for s in clothing_stop):
                        tokens.append(c)
        seen, clean = set(), []
        for t in tokens:
            if t.lower() not in seen:
                seen.add(t.lower())
                clean.append(t)
        return ", ".join(clean)

    @staticmethod
    def extract_scenery_dna_regex(text: str) -> str:
        if not text:
            return ""
        # Поиск явных тегов локации [Location: ...] или Setting (...)
        patterns = [
            r'\[(?:Location|Setting|Environment|Place|Scene)\s*[:=]\s*([^\]]+)\]',
            r'(?:Setting|Location)\s*\(([^)]+)\)'
        ]
        # Стоп-слова времени суток и освещения (они должны определяться световым слоем, а не фоном)
        lighting_time_stops = {
            "night", "day", "dark", "bright", "sunset", "sunrise", "noon", "morning", "evening",
            "midnight", "dim", "light", "lighting", "shadow", "ночь", "день", "утро", "вечер", "свет", "тьма"
        }
        tokens = []
        for p in patterns:
            for m in re.finditer(p, text, re.IGNORECASE):
                for t in m.group(1).replace('"', '').replace("'", "").split(','):
                    c = t.strip()
                    if c and not any(s in c.lower() for s in lighting_time_stops):
                        tokens.append(c)
        seen, clean = set(), []
        for t in tokens:
            if t.lower() not in seen:
                seen.add(t.lower())
                clean.append(t)
        return ", ".join(clean)

    @classmethod
    def parse_json_file(cls, filepath: Union[str, Path]) -> Optional[CharacterCard]:
        filepath = Path(filepath)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.parse_dict(data, source_path=str(filepath.resolve()))
        except Exception as e:
            logger.error(f"[TavernParser] Ошибка парсинга JSON '{filepath}': {e}")
            return None

    @classmethod
    def parse_image_file(cls, filepath: Union[str, Path]) -> Optional[CharacterCard]:
        filepath = Path(filepath)
        try:
            from PIL import Image
            with Image.open(filepath) as img:
                meta = getattr(img, "text", {}) or {}
                raw_meta = meta.get("chara") or meta.get("ccv3")
                if not raw_meta and hasattr(img, "info"):
                    raw_meta = img.info.get("chara") or img.info.get("ccv3")
                if not raw_meta:
                    logger.error(f"[TavernParser] Метаданные персонажа ('chara') не найдены в изображении: {filepath.name}")
                    return None
                if isinstance(raw_meta, bytes):
                    raw_meta = raw_meta.decode("utf-8")
                try:
                    decoded_json = base64.b64decode(raw_meta).decode("utf-8")
                    data = json.loads(decoded_json)
                except Exception:
                    data = json.loads(raw_meta)
            avatar_path = str(filepath.resolve())
            return cls.parse_dict(data, avatar_path=avatar_path, source_path=str(filepath.resolve()))
        except Exception as e:
            logger.error(f"[TavernParser] Ошибка чтения изображения карточки персонажа '{filepath}': {e}")
            return None

    @classmethod
    def parse_file(cls, filepath: Union[str, Path]) -> Optional[CharacterCard]:
        filepath = Path(filepath)
        if not filepath.exists():
            logger.error(f"[TavernParser] Файл карточки не найден: {filepath}")
            return None

        resolved_path = str(filepath.resolve())
        logger.info(f"[TavernParser] Чтение карточки персонажа: '{resolved_path}'")
        suffix = filepath.suffix.lower()
        if suffix == ".json":
            return cls.parse_json_file(filepath)
        elif suffix in [".png", ".webp"]:
            return cls.parse_image_file(filepath)
        else:
            logger.warning(f"[TavernParser] Неподдерживаемый формат карточки: {suffix}")
            return None

    @classmethod
    def parse_dict(
        cls,
        data: Dict[str, Any],
        source_path: Optional[Union[str, Path]] = None,
        avatar_path: Optional[Union[str, Path]] = None
    ) -> CharacterCard:
        """Extracts character metadata across V1, V2, Chub, and Pygmalion schemas."""
        src_path = Path(source_path) if source_path else None
        raw_data = data if isinstance(data, dict) else {}

        # 1. Многоуровневый поиск целевого словаря (Каскад):
        # Разворачиваем возможные обёртки Chub / Venus / V2
        target = raw_data
        candidate_dicts = [raw_data]
        if isinstance(target.get("data"), dict):
            target = target["data"]
            candidate_dicts.append(target)
        if isinstance(target.get("character"), dict):
            target = target["character"]
            candidate_dicts.append(target)
        elif isinstance(target.get("definition"), dict):
            target = target["definition"]
            candidate_dicts.append(target)

        # Поддержка вложенного definition внутри character (формат Chub API)
        if isinstance(target.get("definition"), dict) and target["definition"] not in candidate_dicts:
            target = target["definition"]
            candidate_dicts.append(target)

        # Вспомогательный поиск с фоллбэком на промежуточные узлы и корень raw_data
        def get_field(*keys, default=""):
            for key in keys:
                # Сначала ищем в распакованном target
                if isinstance(target, dict):
                    val = target.get(key)
                    if val and isinstance(val, str) and val.strip():
                        return val.strip()
                # Проверяем промежуточные словари каскада (от глубоких к корню)
                for d in reversed(candidate_dicts):
                    if isinstance(d, dict):
                        v = d.get(key)
                        if v and isinstance(v, str) and v.strip():
                            return v.strip()
                # Если не нашли, проверяем корень raw_data
                if isinstance(raw_data, dict):
                    val_root = raw_data.get(key)
                    if val_root and isinstance(val_root, str) and val_root.strip():
                        return val_root.strip()
            return default

        def get_list_field(*keys):
            for key in keys:
                if isinstance(target, dict):
                    v = target.get(key)
                    if isinstance(v, list) and v:
                        return list(v)
                for d in reversed(candidate_dicts):
                    if isinstance(d, dict):
                        v = d.get(key)
                        if isinstance(v, list) and v:
                            return list(v)
                if isinstance(raw_data, dict):
                    v = raw_data.get(key)
                    if isinstance(v, list) and v:
                        return list(v)
            return []

        # Companions extraction (data.extensions.neonroute.companions or target.extensions...)
        ext_comps = None
        for d in (target, *reversed(candidate_dicts), raw_data):
            if isinstance(d, dict) and isinstance(d.get("extensions"), dict):
                ext_comps = d.get("extensions", {}).get("neonroute", {}).get("companions")
                if ext_comps:
                    break
        if not ext_comps:
            for d in (target, *reversed(candidate_dicts), raw_data):
                if isinstance(d, dict) and isinstance(d.get("companions"), list) and d.get("companions"):
                    ext_comps = d.get("companions")
                    break
        if not isinstance(ext_comps, list):
            ext_comps = []

        # Group Card Detection
        raw_card_tags = get_list_field("tags")
        tags = [str(t).lower() for t in raw_card_tags]
        group_keywords = {"multiple characters", "duo", "group", "party", "trio", "twins", "группа", "дуэт"}
        is_group = (
            bool(group_keywords.intersection(tags))
            or any(isinstance(d, dict) and (d.get("group_only") is True or d.get("is_group") is True) for d in candidate_dicts)
            or (len(ext_comps) > 1)
        )

        # Standalone World Info / Lorebook Detection
        raw_entries = None
        for d in (target, *reversed(candidate_dicts), raw_data):
            if isinstance(d, dict) and "entries" in d:
                raw_entries = d.get("entries")
                break
        has_entries = isinstance(raw_entries, (dict, list)) and len(raw_entries) > 0
        has_char_identity = bool(
            get_field("first_mes", "greeting", "first_message", "char_greeting") or
            get_field("personality", "char_persona", "persona")
        )
        is_lorebook = has_entries and not has_char_identity

        if not is_lorebook and src_path:
            p_lower = src_path.stem.lower()
            if ("world_info" in p_lower or "lorebook" in p_lower) and (has_entries or not has_char_identity):
                is_lorebook = True

        if is_lorebook:
            is_group = False

        # Asset Parsing (CCv3 & Expression Packs)
        backgrounds = []
        expressions = {}
        assets = get_list_field("assets")
        for asset in assets:
            if isinstance(asset, dict):
                a_type = str(asset.get("type", "")).lower()
                a_uri = asset.get("uri") or asset.get("path")
                a_name = str(asset.get("name", "")).lower()
                if a_type in ("background", "scene", "bg") and a_uri:
                    backgrounds.append(str(a_uri))
                elif a_type in ("emotion", "expression", "sprite") and a_uri:
                    expressions[a_name] = str(a_uri)

        # Also check Chub alt_expressions
        alt_expr = None
        for d in (target, *reversed(candidate_dicts), raw_data):
            if isinstance(d, dict) and isinstance(d.get("alt_expressions"), dict):
                alt_expr = d.get("alt_expressions")
                break
        if isinstance(alt_expr, dict):
            for expr_name, expr_url in alt_expr.items():
                if expr_name not in expressions:
                    expressions[str(expr_name).lower()] = str(expr_url)

        # Имя персонажа
        raw_name = get_field("name", "char_name")
        name_lower = str(raw_name).lower()
        tags_set = set(tags)

        # DM / Game Master Detection (rpg removed to prevent false positives)
        dm_tags = {"dungeon master", "game master", "dm", "gm", "narrator", "storyteller", "ttrpg", "text adventure", "cyoa", "мастер игры", "гейммастер", "ведущий", "мастер подземелий"}
        dm_name_tokens = set(re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]+\b', name_lower))
        is_dm = (
            bool(dm_tags.intersection(tags_set))
            or bool({"dm", "gm", "storyteller", "narrator", "гейммастер", "ведущий"}.intersection(dm_name_tokens))
            or "dungeon master" in name_lower
            or "game master" in name_lower
            or "мастер игры" in name_lower
            or any(isinstance(d, dict) and d.get("is_dm") is True for d in candidate_dicts)
        )

        # Scenario / Survival Simulator Detection
        scen_tags = {"scenario", "survival", "simulator", "симулятор", "выживание", "катастрофа", "апокалипсис"}
        is_scenario = (
            bool(scen_tags.intersection(tags_set))
            or "simulator" in name_lower
            or "симулятор" in name_lower
            or "survival" in name_lower
            or "выживание" in name_lower
            or any(isinstance(d, dict) and (d.get("is_scenario") is True or d.get("is_simulator") is True) for d in candidate_dicts)
        )

        # LitRPG / Isekai / System Interface Detection
        sys_tags = {"litrpg", "system", "isekai", "литрпг", "система", "исекай", "status window", "battle song", "интерфейс системы"}
        is_system = (
            bool(sys_tags.intersection(tags_set))
            or "litrpg" in name_lower
            or "литрпг" in name_lower
            or "isekai" in name_lower
            or "исекай" in name_lower
            or any(isinstance(d, dict) and d.get("is_system") is True for d in candidate_dicts)
        )

        # Dating Sim / Romance Progression Detection
        dating_tags = {"dating-sim", "dating sim", "romance", "yandere", "tsundere", "affection", "waifu", "love", "романтика", "дейт-сим"}
        is_dating = (
            bool(dating_tags.intersection(tags_set))
            or "dating sim" in name_lower
            or "dating-sim" in name_lower
            or "дейт-сим" in name_lower
            or any(isinstance(d, dict) and d.get("is_dating") is True for d in candidate_dicts)
        )

        # Psychological Horror / Sanity Detection
        horror_tags = {"horror", "psychological horror", "cosmic horror", "lovecraft", "sanity", "хоррор", "ужасы", "безумие"}
        is_horror = (
            bool(horror_tags.intersection(tags_set))
            or "horror" in name_lower
            or "хоррор" in name_lower
            or any(isinstance(d, dict) and d.get("is_horror") is True for d in candidate_dicts)
        )

        # CYOA / Visual Novel Detection
        cyoa_tags = {"cyoa", "choose your own adventure", "visual novel", "interactive fiction", "текстовый квест", "новелла"}
        is_cyoa = (
            bool(cyoa_tags.intersection(tags_set))
            or "cyoa" in name_lower
            or "visual novel" in name_lower
            or any(isinstance(d, dict) and d.get("is_cyoa") is True for d in candidate_dicts)
        )

        # Reverse Roleplay / Interrogation / Mentorship Detection
        reverse_tags = {"reverse roleplay", "interrogation", "captive", "prisoner", "submissive", "student", "допрос", "пленник", "ученик", "подсудимый"}
        is_reverse_rp = (
            bool(reverse_tags.intersection(tags_set))
            or "interrogation" in name_lower
            or "допрос" in name_lower
            or any(isinstance(d, dict) and d.get("is_reverse_rp") is True for d in candidate_dicts)
        )

        # Detective / Investigation Detection
        detective_tags = {"detective", "investigation", "mystery", "crime", "courtroom", "детектив", "расследование", "улики"}
        is_detective = (
            bool(detective_tags.intersection(tags_set))
            or "detective" in name_lower
            or "детектив" in name_lower
            or any(isinstance(d, dict) and d.get("is_detective") is True for d in candidate_dicts)
        )

        if is_lorebook:
            is_group = False
            is_dm = False
            is_scenario = False
            is_system = False
            is_dating = False
            is_horror = False
            is_cyoa = False
            is_reverse_rp = False
            is_detective = False

        if raw_name:
            name = raw_name
        elif src_path and src_path.stem.lower() not in ("exported", "character_exported", "card", "exported.json", "character"):
            name = src_path.stem.replace("_", " ").title()
        else:
            name = "Персонаж"

        # Извлечение основных атрибутов через get_field с алиасами (макросы {{char}}, {{user}}, <START> не модифицируются)
        description = get_field("description", "char_persona", "persona")
        personality = get_field("personality", "char_persona", "persona")
        scenario = get_field("scenario", "world_scenario", "situation", "context")
        first_mes = get_field("first_mes", "greeting", "first_message", "char_greeting", "initial_message")
        mes_example = get_field("mes_example", "example_dialogue", "examples", "mes_examples")
        system_prompt = get_field("system_prompt", "post_history_instructions")
        post_history_instructions = get_field("post_history_instructions")

        # RU локализации
        first_mes_ru = get_field("first_mes_ru")
        description_ru = get_field("description_ru")
        personality_ru = get_field("personality_ru")
        scenario_ru = get_field("scenario_ru")
        mes_example_ru = get_field("mes_example_ru")

        # Заметки
        creator_notes = get_field("creator_notes", "creatorcomment")
        card_tags = list(tags)

        # Альтернативные приветствия
        alternate_greetings = get_list_field("alternate_greetings", "alt_greetings")
        alternate_greetings_ru = get_list_field("alternate_greetings_ru")

        if not first_mes and alternate_greetings:
            first_mes = str(alternate_greetings[0] or "").strip()

        depth_prompt = (
            get_field("depth_prompt")
            or (target.get("extensions") or {}).get("depth_prompt", {}).get("prompt", "")
            or (data.get("extensions") or {}).get("depth_prompt", {}).get("prompt", "")
            or get_field("character_note")
            or ""
        )

        if not avatar_path and src_path:
            parent_dir = src_path.parent
            safe_clean_name = "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).strip()
            candidate_stems = [
                src_path.stem, name, safe_clean_name,
                f"{safe_clean_name}_avatar", f"{src_path.stem}_avatar"
            ]
            for stem in candidate_stems:
                if not stem:
                    continue
                for ext in [".png", ".jpg", ".jpeg", ".webp", ".PNG", ".JPG", ".JPEG", ".WEBP"]:
                    candidate = parent_dir / f"{stem}{ext}"
                    if candidate.exists() and candidate.is_file():
                        avatar_path = candidate
                        break
                if avatar_path:
                    break

        if not avatar_path:
            avatar_b64 = None
            for d in (target, *reversed(candidate_dicts), raw_data):
                if isinstance(d, dict):
                    b = d.get("avatar") or d.get("image")
                    if b and isinstance(b, str) and len(b) > 100:
                        avatar_b64 = b
                        break
            if avatar_b64 and isinstance(avatar_b64, str) and len(avatar_b64) > 100:
                try:
                    if "," in avatar_b64:
                        avatar_b64 = avatar_b64.split(",", 1)[1]
                    img_data = base64.b64decode(avatar_b64)
                    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
                    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).strip() or "character"
                    extracted_path = CHARACTERS_DIR / f"{safe_name}_avatar.png"
                    with open(extracted_path, "wb") as f_img:
                        f_img.write(img_data)
                    avatar_path = extracted_path
                except Exception as e:
                    logger.warning(f"Could not extract embedded avatar: {e}")

        created_at = (
            get_field("created_at", "create_date")
            or datetime.now().isoformat()
        )

        # Full Ingestion of Embedded Lorebook (character_book / world_info / lorebook)
        entries = cls.extract_lore_entries(data)

        # Сохранение исходной структуры character_book / lorebook
        raw_char_book = None
        for d in (target, *reversed(candidate_dicts), raw_data):
            if isinstance(d, dict):
                for k in ("character_book", "lorebook", "world_info"):
                    b = d.get(k)
                    if isinstance(b, dict) and b:
                        raw_char_book = b
                        break
            if raw_char_book:
                break

        extra_rpg_blocks = []
        rpg_keys = (
            'character_creation', 'core_mechanics', 'system_info', 'rules', 'dm_role',
            'mechanics', 'reputation_system', 'classes', 'races', 'skills', 'archetypes',
            'inventory_system', 'spells', 'threat_levels', 'progression'
        )
        for key in rpg_keys:
            val = None
            for d in (target, *reversed(candidate_dicts), raw_data):
                if isinstance(d, dict) and d.get(key):
                    val = d.get(key)
                    break
            if val and isinstance(val, (dict, list)):
                extra_rpg_blocks.append(f"\n### ПРАВИЛА И МЕХАНИКА [{key.upper()}]:\n" + json.dumps(val, ensure_ascii=False, indent=2))

        rpg_mechanics_str = "\n".join(extra_rpg_blocks).strip() if extra_rpg_blocks else ""
        if extra_rpg_blocks:
            scenario = (scenario or "") + "\n" + "\n".join(extra_rpg_blocks)

        visual_dna = get_field("visual_dna")
        if not visual_dna:
            visual_dna = TavernParser.extract_physical_dna_regex(description)

        scenery_dna = get_field("scenery_dna")
        if not scenery_dna:
            scenery_dna = TavernParser.extract_scenery_dna_regex(scenario or description)

        if is_lorebook and not description:
            entries_cnt = len(entries) if entries else (len(raw_entries) if isinstance(raw_entries, (dict, list)) else 0)
            description = (
                f"Модуль правил и энциклопедия мира (Лорбук).\n"
                f"Содержит {entries_cnt} статей/записей.\n\n"
                f"Подключается к активным диалогам через окно «📜 Правила мира и Лор»."
            )

        resolved_source_path = str(src_path.resolve()) if src_path else (str(source_path) if source_path else None)
        avatar_path_val = str(avatar_path.resolve()) if isinstance(avatar_path, Path) else (str(avatar_path) if avatar_path else None)

        card = CharacterCard(
            name=name,
            description=description,
            personality=personality,
            scenario=scenario,
            first_mes=first_mes,
            first_mes_ru=first_mes_ru,
            description_ru=description_ru,
            personality_ru=personality_ru,
            scenario_ru=scenario_ru,
            mes_example_ru=mes_example_ru,
            mes_example=mes_example,
            creator_notes=creator_notes,
            tags=card_tags,
            alternate_greetings=alternate_greetings,
            alternate_greetings_ru=alternate_greetings_ru,
            avatar_path=avatar_path_val,
            raw_data=data,
            created_at=created_at,
            source_path=resolved_source_path,
            visual_dna=visual_dna,
            scenery_dna=scenery_dna,
            system_prompt=system_prompt,
            post_history_instructions=post_history_instructions,
            depth_prompt=depth_prompt,
            rpg_mechanics_block=rpg_mechanics_str,
            is_group=is_group,
            is_lorebook=is_lorebook,
            is_dm=is_dm,
            is_scenario=is_scenario,
            is_system=is_system,
            is_dating=is_dating,
            is_horror=is_horror,
            is_cyoa=is_cyoa,
            is_reverse_rp=is_reverse_rp,
            is_detective=is_detective,
            backgrounds=backgrounds,
            expressions=expressions,
            lore_entries=entries,
            companions=ext_comps,
            character_book=raw_char_book
        )

        from services.minor_safety import MinorSafetyDetector

        # Определение статуса для основной карточки
        full_text = f"{card.name} {card.description} {card.personality} {card.scenario}"
        card.is_minor = MinorSafetyDetector.is_minor(full_text)

        # Определение статуса для каждого спутника в companions
        if card.companions:
            for comp in card.companions:
                if isinstance(comp, dict):
                    c_text = f"{comp.get('name', '')} {comp.get('description', '')} {comp.get('visual_dna', '')} {comp.get('facts', '')}"
                    comp["is_minor"] = MinorSafetyDetector.is_minor(c_text)
                    if comp.get("is_minor"):
                        card.is_minor = True

        return card

    @staticmethod
    def save_card_companions(card: CharacterCard, json_path: Union[Path, str]) -> bool:
        """Сохраняет сопартийцев карточки по стандарту Tavern в data -> extensions -> neonroute -> companions."""
        try:
            p = Path(json_path)
            if not p.exists():
                logger.warning(f"[TavernParser] Файл карточки для сохранения спутников не найден: {p}")
                return False
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)

            target = data.get("data") if ("spec" in data or "data" in data) and isinstance(data.get("data"), dict) else data

            if "extensions" not in target or not isinstance(target["extensions"], dict):
                target["extensions"] = {}
            if "neonroute" not in target["extensions"] or not isinstance(target["extensions"]["neonroute"], dict):
                target["extensions"]["neonroute"] = {}

            target["extensions"]["neonroute"]["companions"] = getattr(card, "companions", [])

            from utils.file_utils import atomic_write_json
            if not atomic_write_json(p, data):
                return False

            if hasattr(card, "raw_data") and isinstance(card.raw_data, dict):
                card.raw_data = data
            return True
        except Exception as e:
            logger.error(f"[TavernParser] Ошибка сохранения сопартийцев в '{json_path}': {e}")
            return False

    @staticmethod
    def save_card_scenery_dna(card: CharacterCard, json_path: Union[Path, str]) -> bool:
        """Сохраняет card.scenery_dna в JSON файл карточки на диске."""
        try:
            p = Path(json_path)
            if not p.exists():
                return False
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            target = data.get("data", data) if ("spec" in data or "data" in data) else data
            target["scenery_dna"] = getattr(card, "scenery_dna", "")
            if "scenery_dna" in data and data is not target:
                data["scenery_dna"] = getattr(card, "scenery_dna", "")
            from utils.file_utils import atomic_write_json
            return atomic_write_json(p, data)
        except Exception as e:
            logger.warning(f"Could not persist scenery_dna to {json_path}: {e}")
            return False

    @staticmethod
    def _save_card_visual_dna(card: CharacterCard, json_path: Union[Path, str]) -> bool:
        """Сохраняет card.visual_dna в JSON файл карточки на диске."""
        try:
            p = Path(json_path)
            if not p.exists():
                return False
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            target = data.get("data", data) if ("spec" in data or "data" in data) else data
            target["visual_dna"] = getattr(card, "visual_dna", "")
            if "visual_dna" in data and data is not target:
                data["visual_dna"] = getattr(card, "visual_dna", "")
            from utils.file_utils import atomic_write_json
            return atomic_write_json(p, data)
        except Exception as e:
            logger.warning(f"Could not persist visual_dna to {json_path}: {e}")
            return False

    save_card_visual_dna = _save_card_visual_dna

    @staticmethod
    def save_card_translations(card: CharacterCard, file_path: Optional[Path | str] = None) -> bool:
        if file_path:
            p = Path(file_path)
        elif getattr(card, "source_path", None):
            src_p = Path(card.source_path)
            p = src_p if src_p.suffix.lower() == ".json" else (CHARACTERS_DIR / f"{src_p.stem}.json")
        else:
            safe_name = "".join(c for c in card.name if c.isalnum() or c in (' ', '_', '-')).strip() or "character"
            p = CHARACTERS_DIR / f"{safe_name}.json"

        try:
            data = dict(card.raw_data) if isinstance(card.raw_data, dict) and card.raw_data else {}
            if not data and p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}

            if not data or not any(k in data for k in ("name", "data")):
                data = {
                    "spec": "chara_card_v2",
                    "spec_version": "2.0",
                    "data": {
                        "name": card.name,
                        "description": card.description,
                        "personality": card.personality,
                        "scenario": card.scenario,
                        "first_mes": card.first_mes,
                        "mes_example": card.mes_example,
                        "alternate_greetings": list(getattr(card, "alternate_greetings", []) or []),
                        "system_prompt": getattr(card, "system_prompt", ""),
                        "post_history_instructions": getattr(card, "post_history_instructions", ""),
                        "scenery_dna": getattr(card, "scenery_dna", "")
                    }
                }
            target = data.get("data", data) if ("spec" in data or "data" in data) else data

            target["name"] = card.name
            target["description"] = card.description
            target["personality"] = card.personality
            target["scenario"] = card.scenario
            target["first_mes"] = card.first_mes
            target["mes_example"] = card.mes_example
            target["alternate_greetings"] = list(getattr(card, "alternate_greetings", []) or [])
            target["system_prompt"] = getattr(card, "system_prompt", "")

            # Защита и синхронизация полей _ru: если на диске уже есть перевод, а в объекте пусто — не затирать
            for fld in ("description_ru", "personality_ru", "scenario_ru", "mes_example_ru", "first_mes_ru"):
                val = getattr(card, fld, None)
                if not val and target.get(fld):
                    setattr(card, fld, target[fld])

            alts_ru_val = getattr(card, "alternate_greetings_ru", None)
            if not alts_ru_val and target.get("alternate_greetings_ru"):
                card.alternate_greetings_ru = list(target["alternate_greetings_ru"])

            target["description_ru"] = card.description_ru
            target["personality_ru"] = card.personality_ru
            target["scenario_ru"] = card.scenario_ru
            target["mes_example_ru"] = card.mes_example_ru
            target["first_mes_ru"] = card.first_mes_ru
            target["alternate_greetings_ru"] = list(getattr(card, "alternate_greetings_ru", []) or [])
            target["visual_dna"] = getattr(card, "visual_dna", "")
            target["scenery_dna"] = getattr(card, "scenery_dna", "")
            if "scenery_dna" in data and data is not target:
                data["scenery_dna"] = getattr(card, "scenery_dna", "")
            if getattr(card, "companions", None):
                if "extensions" not in target or not isinstance(target["extensions"], dict):
                    target["extensions"] = {}
                if "neonroute" not in target["extensions"] or not isinstance(target["extensions"]["neonroute"], dict):
                    target["extensions"]["neonroute"] = {}
                target["extensions"]["neonroute"]["companions"] = list(card.companions)

            from utils.file_utils import atomic_write_json
            ok = atomic_write_json(p, data)
            if ok:
                card.source_path = str(p.resolve())
                card.raw_data = data
            return ok
        except Exception as e:
            logger.error(f"Failed to save card translations: {e}")
        return False


save_card_companions = TavernParser.save_card_companions
save_card_scenery_dna = TavernParser.save_card_scenery_dna
