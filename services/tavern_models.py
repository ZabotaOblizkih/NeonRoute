"""
Data models for Tavern/Chub character cards and world lorebooks:
- LoreEntry: Dataclass representing a lorebook/world-info article.
- CharacterCard: Dataclass representing a character card with persona, greetings, and lorebook.
"""

from __future__ import annotations

import re
import json
import uuid
import logging
from dataclasses import dataclass, field, asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Union, List

from config import CHARACTERS_DIR

logger = logging.getLogger("NeonRoute.TavernModels")

__all__ = [
    "LoreEntry",
    "CharacterCard",
]

@dataclass
class LoreEntry:
    keys: list[str] = field(default_factory=list)
    content: str = ""
    comment: str = ""
    enabled: bool = True
    constant: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.comment

    @property
    def text(self) -> str:
        return self.content

    @property
    def key(self) -> list[str]:
        return self.keys

    def to_dict(self) -> dict:
        return {
            "keys": list(self.keys),
            "content": self.content,
            "comment": self.comment,
            "enabled": self.enabled,
            "constant": self.constant,
            "raw": dict(self.raw) if isinstance(self.raw, dict) else self.raw,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "LoreEntry":
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            return cls()
        raw_keys = data.get("keys") or data.get("key") or []
        if isinstance(raw_keys, (list, tuple, set)):
            keys = [str(k).strip() for k in raw_keys if str(k).strip()]
        elif isinstance(raw_keys, (str, int, float)):
            keys = [k.strip() for k in str(raw_keys).split(",") if k.strip()]
        else:
            keys = []
        return cls(
            keys=keys,
            content=str(data.get("content") or data.get("text") or ""),
            comment=str(data.get("comment") or data.get("name") or ""),
            enabled=bool(data.get("enabled", True)),
            constant=bool(data.get("constant", False) or data.get("always_active", False)),
            raw=dict(data.get("raw", data)) if isinstance(data.get("raw"), dict) else (dict(data) if isinstance(data, dict) else {}),
        )

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.raw.get(key, default)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        return self.raw[item]

    def __contains__(self, item: str) -> bool:
        if item in ("keys", "key", "content", "text", "comment", "name", "enabled", "constant", "raw"):
            return True
        return item in self.raw


class CharacterCard:
    def __init__(
        self,
        name: str = "Assistant",
        description: str = "",
        personality: str = "",
        scenario: str = "",
        first_mes: str = "",
        first_mes_ru: str = "",
        description_ru: str = "",
        personality_ru: str = "",
        scenario_ru: str = "",
        mes_example_ru: str = "",
        mes_example: str = "",
        creator_notes: str = "",
        tags: Optional[list] = None,
        alternate_greetings: Optional[list] = None,
        alternate_greetings_ru: Optional[list] = None,
        avatar_path: Optional[Union[Path, str]] = None,
        raw_data: Optional[Dict[str, Any]] = None,
        character_id: Optional[str] = None,
        created_at: Optional[str] = None,
        source_path: Optional[Union[Path, str]] = None,
        visual_dna: str = "",
        scenery_dna: str = "",
        system_prompt: str = "",
        post_history_instructions: str = "",
        depth_prompt: str = "",
        rpg_mechanics_block: str = "",
        is_group: bool = False,
        is_lorebook: bool = False,
        is_dm: bool = False,
        is_scenario: bool = False,
        is_system: bool = False,
        is_dating: bool = False,
        is_horror: bool = False,
        is_cyoa: bool = False,
        is_reverse_rp: bool = False,
        is_detective: bool = False,
        backgrounds: Optional[list] = None,
        expressions: Optional[dict] = None,
        lore_entries: Optional[List[LoreEntry]] = None,
        companions: Optional[List[Dict[str, Any]]] = None,
        is_male: Optional[bool] = None,
        is_minor: bool = False,
        character_book: Optional[Dict[str, Any]] = None,
        **kwargs: Any
    ):
        self._is_male: Optional[bool] = is_male
        self.is_minor: bool = is_minor
        self.character_book = character_book or kwargs.get("character_book") or kwargs.get("lorebook")
        self.id = character_id or f"char_{uuid.uuid4().hex[:8]}"
        self.name = name or "Unknown Persona"
        self.description = description
        self.personality = personality
        self.scenario = scenario
        self.first_mes = first_mes
        self.first_mes_ru = first_mes_ru
        self.description_ru = description_ru
        self.personality_ru = personality_ru
        self.scenario_ru = scenario_ru
        self.mes_example_ru = mes_example_ru
        self.mes_example = mes_example
        self.creator_notes = creator_notes
        self.tags = tags or []
        self.alternate_greetings = alternate_greetings or []
        self.alternate_greetings_ru = alternate_greetings_ru or []
        self.avatar_path = avatar_path
        self.raw_data = raw_data or {}
        self.created_at = created_at or datetime.now().isoformat()
        self.source_path = source_path
        self.visual_dna = (visual_dna or "").strip()
        self.scenery_dna = (scenery_dna or "").strip()
        self.system_prompt = (system_prompt or "").strip()
        self.post_history_instructions = (post_history_instructions or "").strip()
        self.depth_prompt = (depth_prompt or "").strip()
        self.rpg_mechanics_block = (rpg_mechanics_block or "").strip()
        self.is_group = is_group
        self.is_lorebook = is_lorebook
        self.is_dm = is_dm
        self.is_scenario = is_scenario
        self.is_system = is_system
        self.is_dating = is_dating
        self.is_horror = is_horror
        self.is_cyoa = is_cyoa
        self.is_reverse_rp = is_reverse_rp
        self.is_detective = is_detective
        self.backgrounds = backgrounds or []
        self.expressions = expressions or {}
        self.lore_entries: List[LoreEntry] = [
            LoreEntry.from_dict(e) if isinstance(e, dict) else e
            for e in (lore_entries or [])
        ]
        self.companions: List[Dict[str, Any]] = list(companions) if companions else []

        # Санация ложных _ru полей (когда в _ru скопирован английский оригинал без кириллицы)
        for field_base in ("description", "personality", "scenario", "mes_example", "first_mes"):
            ru_val = getattr(self, f"{field_base}_ru", "") or ""
            orig_val = getattr(self, field_base, "") or ""
            if ru_val and (ru_val.strip() == orig_val.strip() or not re.search(r'[а-яА-ЯёЁ]{3,}', ru_val)):
                setattr(self, f"{field_base}_ru", "")
        if getattr(self, "alternate_greetings_ru", None):
            sanitized_alts = []
            for idx, alt_ru in enumerate(self.alternate_greetings_ru):
                orig_alt = self.alternate_greetings[idx] if idx < len(self.alternate_greetings) else ""
                if alt_ru and (alt_ru.strip() == str(orig_alt).strip() or not re.search(r'[а-яА-ЯёЁ]{3,}', str(alt_ru))):
                    sanitized_alts.append("")
                else:
                    sanitized_alts.append(alt_ru)
            self.alternate_greetings_ru = sanitized_alts

    @property
    def archetype_label(self) -> str:
        """Единый источник правды для названий архетипов карточки в UI и логах."""
        if self.is_system:
            return "System (ЛитРПГ/Исекай)"
        elif self.is_dating:
            return "Dating-Sim (Симпатия)"
        elif self.is_horror:
            return "Horror (Рассудок)"
        elif self.is_cyoa:
            return "CYOA (Интерактивная новелла)"
        elif self.is_reverse_rp:
            return "Reverse-RP (Зависимый персонаж)"
        elif self.is_detective:
            return "Detective (Расследование)"
        elif self.is_dm:
            return "DM (Гейммастер)"
        elif self.is_scenario:
            return "Scenario (Выживание)"
        elif self.is_group:
            return "Group (Группа спутников)"
        elif self.is_lorebook:
            return "Lorebook (Энциклопедия)"
        return "Solo (Одиночный персонаж)"

    @property
    def is_environment(self) -> bool:
        """True для карточек среды, мира, квеста или системы (без физического тела бота)."""
        return bool(self.is_system or self.is_dm or self.is_cyoa or self.is_scenario or self.is_lorebook)

    @property
    def is_male(self) -> bool:
        """Определяет мужской пол персонажа с поддержкой EN/RU тегов и местоимений."""
        if getattr(self, "_is_male", None) is not None:
            return bool(self._is_male)
        tags_lower = [str(t).lower() for t in self.tags]
        if any(t in ("male", "man", "guy", "boy", "мужчина", "парень") for t in tags_lower):
            return True
        if any(t in ("female", "woman", "girl", "waifu", "женщина", "девушка") for t in tags_lower):
            return False

        text = f"{self.name} {self.description} {self.personality} {self.scenario}".lower()
        male_markers = (r'\bhe\b', r'\bhim\b', r'\bhis\b', r'\bmale\b', r'\bman\b', r'\bboy\b',
                        r'\bмужчина\b', r'\bпарень\b', r'\bюноша\b', r'\bон сам\b')
        female_markers = (r'\bshe\b', r'\bher\b', r'\bhers\b', r'\bfemale\b', r'\bwoman\b', r'\bgirl\b',
                          r'\bженщина\b', r'\bдевушка\b', r'\bона сама\b')
        male_score = sum(len(re.findall(p, text)) for p in male_markers)
        female_score = sum(len(re.findall(p, text)) for p in female_markers)
        return male_score > female_score

    @is_male.setter
    def is_male(self, value: Optional[bool]) -> None:
        self._is_male = value

    @property
    def narrative_pov(self) -> str:
        """
        Определяет манеру повествования карточки: 1-е лицо ('first') vs 3-е лицо ('third').
        Анализирует текст действий и описаний (внутри *...* или вне прямой речи) в приветствии и примерах реплик.
        """
        greeting = (self.first_mes_ru or self.first_mes or "").strip()
        mes_ex = (self.mes_example_ru or self.mes_example or "").strip()
        sample_text = f"{greeting}\n{mes_ex}"
        if not sample_text.strip():
            sample_text = f"{self.description_ru or self.description}\n{self.personality_ru or self.personality}"

        # Извлекаем действия внутри *...* если есть, иначе берем весь текст
        action_parts = re.findall(r'\*([^*]+)\*', sample_text)
        analysis_text = " ".join(action_parts) if action_parts else sample_text

        # Удаляем реплики в кавычках (прямую речь диалогов), чтобы проверять именно повествование
        text_no_dialogue = re.sub(r'["«][^"»]+["»]', ' ', analysis_text)
        text_lower = text_no_dialogue.lower()

        # Маркеры 1-го лица: я, мне, меня, мной, мною, мой, моя, мое, моё, мои, i, me, my, mine, myself
        first_person_patterns = (
            r'\bя\b', r'\bмне\b', r'\bменя\b', r'\bмной\b', r'\bмною\b',
            r'\bмой\b', r'\bмоя\b', r'\bмое\b', r'\bмоё\b', r'\bмои\b',
            r'\bi\b', r'\bme\b', r'\bmy\b', r'\bmine\b', r'\bmyself\b'
        )
        first_count = sum(len(re.findall(p, text_lower)) for p in first_person_patterns)

        # Маркеры 3-го лица: он, она, оно, они, его, её, ее, ему, ей, им, ним, ней, ними, he, him, his, she, her, hers, they, them, their
        third_person_patterns = (
            r'\bон\b', r'\bона\b', r'\bоно\b', r'\bони\b',
            r'\bего\b', r'\bеё\b', r'\bее\b', r'\bему\b', r'\bей\b', r'\bим\b',
            r'\bним\b', r'\bней\b', r'\bними\b',
            r'\bhe\b', r'\bhim\b', r'\bhis\b', r'\bshe\b', r'\bher\b', r'\bhers\b',
            r'\bthey\b', r'\bthem\b', r'\btheir\b'
        )
        third_count = sum(len(re.findall(p, text_lower)) for p in third_person_patterns)

        # Упоминание имени персонажа в повествовании — сильнейший признак 3-го лица
        if self.name and len(self.name) >= 2:
            name_low = self.name.lower()
            third_count += len(re.findall(rf'\b{re.escape(name_low)}\b', text_lower)) * 2

        if first_count > third_count:
            return "first"
        return "third"

    def get_greetings_pool(self) -> List[Dict[str, Any]]:
        """
        Единый источник правды для всех вариантов завязок карточки.
        Гарантирует строгое совпадение индексов оригиналов и русских переводов.
        """
        pool = []
        # Индекс 0: Основное приветствие
        orig_0 = (self.first_mes or "").strip()
        ru_0 = (self.first_mes_ru or "").strip()
        if orig_0 or ru_0:
            pool.append({
                "index": 0,
                "orig": orig_0,
                "ru": ru_0,
                "effective": ru_0 or orig_0
            })

        # Индексы 1..N: Альтернативные приветствия
        alts_orig = getattr(self, "alternate_greetings", []) or []
        alts_ru = getattr(self, "alternate_greetings_ru", []) or []

        max_len = max(len(alts_orig), len(alts_ru))
        for i in range(max_len):
            orig_i = (alts_orig[i] or "").strip() if i < len(alts_orig) else ""
            ru_i = (alts_ru[i] or "").strip() if i < len(alts_ru) else ""
            if orig_i or ru_i:
                pool.append({
                    "index": len(pool),
                    "orig": orig_i,
                    "ru": ru_i,
                    "effective": ru_i or orig_i
                })

        return pool

    def set_greeting_variant(self, index: int, text: str, is_ru: bool = False):
        """Сохраняет текст приветствия строго в нужный слот по индексу."""
        text = (text or "").strip()
        if index == 0:
            if is_ru:
                self.first_mes_ru = text
            else:
                self.first_mes = text
        else:
            alt_idx = index - 1
            if is_ru:
                if not hasattr(self, "alternate_greetings_ru") or not isinstance(self.alternate_greetings_ru, list):
                    self.alternate_greetings_ru = []
                while len(self.alternate_greetings_ru) <= alt_idx:
                    self.alternate_greetings_ru.append("")
                self.alternate_greetings_ru[alt_idx] = text
            else:
                if not hasattr(self, "alternate_greetings") or not isinstance(self.alternate_greetings, list):
                    self.alternate_greetings = []
                while len(self.alternate_greetings) <= alt_idx:
                    self.alternate_greetings.append("")
                self.alternate_greetings[alt_idx] = text

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация карточки для сохранения в JSON сессии."""
        res = {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_mes,
            "first_mes_ru": self.first_mes_ru,
            "description_ru": self.description_ru,
            "personality_ru": self.personality_ru,
            "scenario_ru": self.scenario_ru,
            "mes_example_ru": self.mes_example_ru,
            "mes_example": self.mes_example,
            "creator_notes": self.creator_notes,
            "tags": self.tags,
            "alternate_greetings": self.alternate_greetings,
            "alternate_greetings_ru": self.alternate_greetings_ru,
            "avatar_path": str(self.avatar_path) if self.avatar_path else self.avatar_path,
            "source_path": str(self.source_path) if self.source_path else self.source_path,
            "visual_dna": self.visual_dna,
            "scenery_dna": self.scenery_dna,
            "system_prompt": self.system_prompt,
            "post_history_instructions": self.post_history_instructions,
            "depth_prompt": self.depth_prompt,
            "rpg_mechanics_block": self.rpg_mechanics_block,
            "is_group": self.is_group,
            "is_lorebook": self.is_lorebook,
            "is_dm": self.is_dm,
            "is_scenario": self.is_scenario,
            "is_system": self.is_system,
            "is_dating": self.is_dating,
            "is_horror": self.is_horror,
            "is_cyoa": self.is_cyoa,
            "is_reverse_rp": self.is_reverse_rp,
            "is_detective": self.is_detective,
            "is_minor": self.is_minor,
            "backgrounds": self.backgrounds,
            "expressions": self.expressions,
            "lore_entries": [
                e.to_dict() if hasattr(e, "to_dict") else asdict(e) if is_dataclass(e) else e
                for e in self.lore_entries
            ],
            "companions": self.companions,
            "created_at": self.created_at
        }
        if getattr(self, "character_book", None):
            res["character_book"] = self.character_book
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CharacterCard":
        """Десериализация карточки из сохраненного словаря сессии с каноническим self-healing."""
        if not isinstance(data, dict):
            return cls(name="Неизвестный")

        raw_lore = data.get("lore_entries") or []
        lore_entries = [
            LoreEntry.from_dict(e) if isinstance(e, dict) else e
            for e in raw_lore
        ]
        if not lore_entries and ("character_book" in data or "world_info" in data or "entries" in data or (isinstance(data.get("data"), dict) and any(k in data["data"] for k in ("character_book", "world_info", "entries")))):
            lore_entries = TavernParser.extract_lore_entries(data)

        ext_data = data.get("extensions")
        ext_dict = ext_data if isinstance(ext_data, dict) else {}
        inner_data = data.get("data")
        inner_dict = inner_data if isinstance(inner_data, dict) else {}
        inner_ext = inner_dict.get("extensions")
        inner_ext_dict = inner_ext if isinstance(inner_ext, dict) else {}

        raw_companions = (
            data.get("companions")
            or ext_dict.get("neonroute", {}).get("companions", [])
            or inner_ext_dict.get("neonroute", {}).get("companions", [])
        )
        if not isinstance(raw_companions, list):
            raw_companions = []

        depth_prompt = (
            data.get("depth_prompt")
            or inner_dict.get("depth_prompt")
            or ext_dict.get("depth_prompt", {}).get("prompt", "")
            or inner_ext_dict.get("depth_prompt", {}).get("prompt", "")
            or data.get("character_note")
            or inner_dict.get("character_note")
            or ""
        )

        card = cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            personality=data.get("personality", ""),
            scenario=data.get("scenario", ""),
            first_mes=data.get("first_mes", ""),
            first_mes_ru=data.get("first_mes_ru", ""),
            description_ru=data.get("description_ru", ""),
            personality_ru=data.get("personality_ru", ""),
            scenario_ru=data.get("scenario_ru", ""),
            mes_example_ru=data.get("mes_example_ru", ""),
            mes_example=data.get("mes_example", ""),
            creator_notes=data.get("creator_notes", ""),
            tags=data.get("tags") or [],
            alternate_greetings=data.get("alternate_greetings") or [],
            alternate_greetings_ru=data.get("alternate_greetings_ru") or [],
            avatar_path=data.get("avatar_path", ""),
            raw_data=data,
            created_at=data.get("created_at"),
            source_path=data.get("source_path", ""),
            visual_dna=data.get("visual_dna", ""),
            scenery_dna=data.get("scenery_dna", ""),
            system_prompt=data.get("system_prompt", ""),
            post_history_instructions=data.get("post_history_instructions", ""),
            depth_prompt=depth_prompt,
            rpg_mechanics_block=data.get("rpg_mechanics_block", ""),
            is_group=data.get("is_group", False),
            is_lorebook=data.get("is_lorebook", False),
            is_dm=data.get("is_dm", False),
            is_scenario=data.get("is_scenario", False),
            is_system=data.get("is_system", False),
            is_dating=data.get("is_dating", False),
            is_horror=data.get("is_horror", False),
            is_cyoa=data.get("is_cyoa", False),
            is_reverse_rp=data.get("is_reverse_rp", False),
            is_detective=data.get("is_detective", False),
            is_minor=bool(data.get("is_minor", False)),
            backgrounds=data.get("backgrounds") or [],
            expressions=data.get("expressions") or {},
            lore_entries=lore_entries,
            companions=raw_companions,
            character_book=data.get("character_book") or data.get("lorebook")
        )

        if "is_minor" not in data:
            from services.minor_safety import MinorSafetyDetector
            full_text = f"{card.name} {card.description} {card.personality} {card.scenario}"
            card.is_minor = MinorSafetyDetector.is_minor(full_text)

        if card.companions:
            from services.minor_safety import MinorSafetyDetector
            for comp in card.companions:
                if isinstance(comp, dict):
                    if "is_minor" not in comp:
                        c_text = f"{comp.get('name', '')} {comp.get('description', '')}"
                        comp["is_minor"] = MinorSafetyDetector.is_minor(c_text)
                    if comp.get("is_minor"):
                        card.is_minor = True  # Агрегируем флаг безопасности на весь объект карточки

        if hasattr(card, "companions") and isinstance(card.companions, list) and len(card.companions) > 1:
            card.is_group = True

        # Self-Healing: синхронизация карточки с файлом на диске
        candidate_p = None
        if card.source_path and Path(card.source_path).exists():
            candidate_p = Path(card.source_path)
        elif card.name:
            # Поиск по имени, если исходный путь потерян или не задан
            cand_exact = CHARACTERS_DIR / f"{card.name}.json"
            safe_name = "".join(c for c in card.name if c.isalnum() or c in (' ', '_', '-')).strip()
            cand_safe = CHARACTERS_DIR / f"{safe_name}.json" if safe_name else None

            if cand_exact.exists() and cand_exact.is_file():
                candidate_p = cand_exact
            elif cand_safe and cand_safe.exists() and cand_safe.is_file():
                candidate_p = cand_safe
            elif CHARACTERS_DIR.exists():
                c_name_lower = card.name.strip().lower()
                c_id = getattr(card, "id", None)
                for f_cand in CHARACTERS_DIR.glob("*.json"):
                    if f_cand.suffix == ".tmp":
                        continue
                    try:
                        with open(f_cand, "r", encoding="utf-8") as jf:
                            cd_raw = json.load(jf)
                        target_meta = cd_raw.get("data", cd_raw) if ("spec" in cd_raw or "data" in cd_raw) else cd_raw
                        disk_name = str(target_meta.get("name") or cd_raw.get("name") or "").strip().lower()
                        disk_id = str(target_meta.get("id") or cd_raw.get("id") or "").strip()
                        if c_id and disk_id and c_id != disk_id:
                            continue
                        if (c_id and disk_id == c_id) or (disk_name and disk_name == c_name_lower and not disk_id):
                            candidate_p = f_cand
                            break
                    except Exception:
                        continue

        if candidate_p and candidate_p.exists():
                try:
                    with open(candidate_p, "r", encoding="utf-8") as f:
                        disk_raw = json.load(f)
                    from services.tavern_parser import TavernParser
                    disk_card = TavernParser.parse_dict(disk_raw, source_path=candidate_p)
                    if disk_card:
                        card.source_path = str(candidate_p.resolve())
                        card.system_prompt = card.system_prompt or disk_card.system_prompt
                        card.is_system = disk_card.is_system
                        card.is_dm = disk_card.is_dm
                        card.is_scenario = disk_card.is_scenario
                        card.is_group = disk_card.is_group
                        card.is_dating = disk_card.is_dating
                        card.is_horror = disk_card.is_horror
                        card.is_cyoa = disk_card.is_cyoa
                        card.is_reverse_rp = disk_card.is_reverse_rp
                        card.is_detective = disk_card.is_detective
                        card.is_lorebook = disk_card.is_lorebook
                        card.is_minor = disk_card.is_minor
                        if not card.visual_dna and disk_card.visual_dna:
                            card.visual_dna = disk_card.visual_dna
                        if not card.scenery_dna and getattr(disk_card, "scenery_dna", None):
                            card.scenery_dna = disk_card.scenery_dna
                        if not card.lore_entries and disk_card.lore_entries:
                            card.lore_entries = disk_card.lore_entries
                        if not card.companions and getattr(disk_card, "companions", None):
                            card.companions = disk_card.companions
                            if len(card.companions) > 1:
                                card.is_group = True

                        # ---------------------------------------------------------
                        # Self-Healing: синхронизация локализации (_ru) с диска
                        # ---------------------------------------------------------
                        loc_fields = [
                            'description_ru',
                            'personality_ru',
                            'scenario_ru',
                            'first_mes_ru',
                            'mes_example_ru',
                        ]
                        for field in loc_fields:
                            s_val = getattr(card, field, None)
                            d_val = getattr(disk_card, field, None)
                            if (s_val is None or (isinstance(s_val, str) and not s_val.strip())) and d_val:
                                setattr(card, field, d_val)

                        s_alts = getattr(card, 'alternate_greetings_ru', None) or []
                        d_alts = getattr(disk_card, 'alternate_greetings_ru', None) or []
                        if not any(bool(alt and alt.strip()) for alt in s_alts) and d_alts:
                            card.alternate_greetings_ru = list(d_alts)
                        elif s_alts and d_alts and len(s_alts) < len(d_alts):
                            m_alts = list(s_alts)
                            for i in range(len(s_alts), len(d_alts)):
                                m_alts.append(d_alts[i])
                            card.alternate_greetings_ru = m_alts
                except Exception as e:
                    logger.debug(f"[CharacterCard] Self-healing error for '{card.name}': {e}")

        return card

    def get_system_prompt(self, user_name: str = "User", include_custom_system: bool = True) -> str:
        """Возвращает фактологическое наполнение карточки. В базовый промпт включает только постоянные (constant) статьи лорбука."""
        from services.macro_engine import MacroEngine

        desc = self.description_ru or self.description
        pers = self.personality_ru or self.personality
        scen = self.scenario_ru or self.scenario
        examples = self.mes_example_ru or self.mes_example

        # Гарантированное добавление rpg_mechanics_block даже если заполнено scenario_ru!
        if self.rpg_mechanics_block and self.rpg_mechanics_block not in scen:
            scen = f"{scen}\n\n{self.rpg_mechanics_block}".strip()

        sections = []
        if desc:
            sections.append(f"### Описание и внешность:\n{desc.strip()}")

        # Если у карточки есть creator_notes и при этом description короткое (< 300 символов),
        # а creator_notes содержит полезный лор/правила (не просто ссылки на соцсети)
        if self.creator_notes and self.creator_notes.strip():
            cn_strip = self.creator_notes.strip()
            if len(desc.strip()) < 300 and cn_strip not in desc and not re.match(r'^(?:https?://|www\.|discord|patreon|ko-fi)', cn_strip, re.IGNORECASE):
                sections.append(f"### Дополнительный лор и заметки создателя:\n{cn_strip}")

        if pers:
            sections.append(f"### Личность и поведение:\n{pers.strip()}")
        if scen:
            sections.append(f"### Сценарий и обстановка:\n{scen.strip()}")
        if self.lore_entries:
            constant_lore = [
                e.content.strip()
                for e in self.lore_entries
                if getattr(e, "enabled", True)
                and e.content.strip()
                and (getattr(e, "constant", False) or not getattr(e, "keys", []))
            ]
            if constant_lore:
                sections.append("### Лор и правила мира (Lorebook):\n" + "\n\n".join(constant_lore))
        if include_custom_system and self.system_prompt:
            sections.append(f"### Авторские правила:\n{self.system_prompt.strip()}")
        if getattr(self, "depth_prompt", None) and self.depth_prompt.strip():
            sections.append(f"### Ключевая заметка автора (Character Note):\n{self.depth_prompt.strip()}")
        if examples:
            sections.append(f"### Примеры реплик:\n{examples.strip()}")

        raw_prompt = "\n\n".join(sections)
        return MacroEngine.process(raw_prompt, user_name=user_name, char_name=self.name)

    def get_triggered_lore_entries(self, context_text: str, user_name: str = "User") -> List[str]:
        """Возвращает список динамических записей встроенного лорбука карточки, сработавших по ключам в контексте."""
        if not self.lore_entries or not context_text:
            return []
        from services.macro_engine import MacroEngine
        ctx_lower = context_text.lower()
        triggered = []
        for e in self.lore_entries:
            if not getattr(e, "enabled", True) or not e.content.strip():
                continue
            if getattr(e, "constant", False) or not getattr(e, "keys", []):
                continue
            raw_dict = getattr(e, "raw", {}) if isinstance(getattr(e, "raw", {}), dict) else {}
            case_sens = bool(raw_dict.get("case_sensitive", False))
            haystack = context_text if case_sens else ctx_lower
            keys = [str(k).strip() if case_sens else str(k).lower().strip() for k in getattr(e, "keys", []) if str(k).strip()]
            if not any(k in haystack for k in keys):
                continue
            sec_keys = [str(k).strip() if case_sens else str(k).lower().strip() for k in (raw_dict.get("secondary_keys") or []) if str(k).strip()]
            if sec_keys and not any(sk in haystack for sk in sec_keys):
                continue
            order = int(raw_dict.get("insertion_order", raw_dict.get("priority", 100)) or 100)
            processed = MacroEngine.process(e.content.strip(), user_name=user_name, char_name=self.name)
            triggered.append((order, processed))

        triggered.sort(key=lambda x: x[0])
        return [text for _, text in triggered[:8]]


