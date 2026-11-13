from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Union


class TargetSubject(str, Enum):
    ELARIA = "elaria"            # Автопортрет Эларии в режиме Ассистента
    COMPANION = "companion"      # Спутница в режиме Roleplay
    SCENE_OBJECT = "scene_object"# Внешний объект: орк, пейзаж, предмет, мужчина


@dataclass
class SceneAnalysisContract:
    framing: str = "medium"
    focal_depth: str = "medium"
    atmosphere: str = "clear"
    orientation: str = "portrait"
    is_surreal_or_magic: bool = False
    is_monster_or_creature: bool = False
    has_complex_fabrics: bool = False
    has_tech_or_vehicles: bool = False
    is_cinematic_raw_photo: bool = False
    is_nsfw: bool = False
    is_anime_2d: bool = False
    english_tags: str = ""
    scene_type: str = "humanoid"  # Биологический тип объекта для классификатора
    subject_count: str = "solo"


@dataclass
class VisualIntent:
    prompt: str
    target_subject: TargetSubject = TargetSubject.SCENE_OBJECT
    user_neg: str = ""
    suggested_model: Optional[str] = None
    seed: int = -1
    aspect_ratio: Optional[str] = None
    scene_type: str = "dialogue"  # Кино-архетип: intimate | action | dialogue | exploration
    mutations: List[str] = field(default_factory=list)
    raw_user_query: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    mode: Optional[str] = None
    companion_dna: Optional[str] = None
    companion_outfit: Optional[str] = None
    is_scene_background: bool = False
    location_dna: Optional[str] = None
    scene_ambience: Optional[str] = None
    character_card: Optional[Any] = None
    status: str = "ready"

    @property
    def is_self_portrait(self) -> bool:
        return self.target_subject == TargetSubject.ELARIA

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["target_subject"] = self.target_subject.value
        d["is_self_portrait"] = self.is_self_portrait
        cc = d.get("character_card")
        if cc is not None and not isinstance(cc, dict):
            d["character_card"] = cc.to_dict() if hasattr(cc, "to_dict") else None
        return d

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    @classmethod
    def from_dict(cls, data: Union[Dict[str, Any], 'VisualIntent']) -> 'VisualIntent':
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            return cls(prompt=str(data or ""))

        raw_subj = data.get("target_subject")
        if isinstance(raw_subj, TargetSubject):
            subject = raw_subj
        elif raw_subj in ("elaria", TargetSubject.ELARIA.value):
            subject = TargetSubject.ELARIA
        elif raw_subj in ("companion", TargetSubject.COMPANION.value):
            subject = TargetSubject.COMPANION
        elif data.get("is_self_portrait"):
            subject = TargetSubject.ELARIA
        else:
            subject = TargetSubject.SCENE_OBJECT

        # Нормализация кино-архетипа
        raw_scene_type = str(data.get("scene_type") or "dialogue").lower().strip()
        if raw_scene_type not in ("intimate", "action", "dialogue", "exploration"):
            raw_scene_type = "dialogue"

        return cls(
            prompt=str(data.get("prompt") or "").strip(),
            target_subject=subject,
            user_neg=str(data.get("user_neg") or "").strip(),
            suggested_model=data.get("suggested_model"),
            seed=int(data.get("seed", -1)),
            aspect_ratio=data.get("aspect_ratio") or data.get("ar"),
            scene_type=raw_scene_type,
            mutations=list(data.get("mutations") or []),
            raw_user_query=data.get("raw_user_query"),
            session_id=data.get("session_id"),
            message_id=data.get("message_id"),
            mode=data.get("mode"),
            companion_dna=data.get("companion_dna"),
            companion_outfit=data.get("companion_outfit"),
            is_scene_background=bool(data.get("is_scene_background", False)),
            location_dna=data.get("location_dna"),
            scene_ambience=data.get("scene_ambience"),
            status=str(data.get("status", "ready"))
        )
