"""
NeonRoute - Unified Forge Models Registry (Single Source of Truth)
Определяет паспорта моделей, векторы возможностей латентного пространства
и авторские канонические параметры инференса.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple


@dataclass(frozen=True)
class ModelCapabilities:
    """Вектор компетенций модели в латентном пространстве (0.0 - 1.0)."""
    surrealism_vfx: float = 0.0          # Магия, светящиеся глаза, ауры, огонь, эфир
    monsters_creatures: float = 0.0      # Монстры, демоны, нечеловеческая анатомия, химеры
    hard_surface_tech: float = 0.0       # Машины, звездолеты, мехи, скафандры, металл
    complex_fabrics: float = 0.0         # Бальные платья, кружево, корсеты, косплей, шелк
    cinematic_raw: float = 0.0           # Пленочное зерно, скалы, горы, брутализм, аналоговый свет
    photoreal_human: float = 0.0         # Текстура пор кожи, реальные люди, живой взгляд
    nsfw_photoreal: float = 0.0          # Будуар, студийное ню, естественная анатомия тела
    nsfw_stylized: float = 0.0           # 2.5D/аниме эротика, пин-ап, стилизованные формы
    anime_2d: float = 0.0                # Чистый 2D контур, манга, селл-шейдинг
    deep_focus_optics: float = 0.0       # Четкость дальних планов, ландшафты, f/8-f/11


@dataclass(frozen=True)
class AuthorCanon:
    """Инженерный канон автора чекпоинта."""
    default_cfg: float
    min_cfg: float
    max_cfg: float
    recommended_steps: int
    sampler_name: str
    scheduler: str
    clip_skip: int
    allow_adetailer: bool
    default_ad_denoise: float
    ad_prompt: str
    ad_negative: str
    base_negative: str
    prompt_prefix: str
    hr_upscaler: str
    default_hr_scale: float = 1.25
    default_hr_denoise: float = 0.35
    hr_second_pass_steps: int = 10


@dataclass(frozen=True)
class ModelProfile:
    """Полный паспорт модели."""
    id: str
    title: str
    description: str
    canonical_filename: str
    capabilities: ModelCapabilities
    canon: AuthorCanon
    filename_aliases: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def model_key(self) -> str:
        return self.id

    @property
    def cfg_scale(self) -> float:
        return self.canon.default_cfg

    @property
    def steps(self) -> int:
        return self.canon.recommended_steps

    @property
    def sampler_name(self) -> str:
        return self.canon.sampler_name

    @property
    def hires_denoise(self) -> float:
        return self.canon.default_hr_denoise


# ── ЕДИНЫЙ РЕЕСТР ПАСПОРТОВ МОДЕЛЕЙ (SINGLE SOURCE OF TRUTH) ──────────────────

MODEL_REGISTRY: Dict[str, ModelProfile] = {
    "realistic": ModelProfile(
        id="realistic",
        title="Realistic Vision v6.0",
        description="Аналоговое фото, скалы, горы, глубокий фокус, твердый sci-fi, реальные люди.",
        canonical_filename="realisticVisionV60B1_v51HyperVAE_pruned_fp16.safetensors",
        capabilities=ModelCapabilities(
            surrealism_vfx=0.15,
            monsters_creatures=0.10,
            hard_surface_tech=0.95,      # Идеально для реальных машин, танков, звездолетов
            complex_fabrics=0.75,
            cinematic_raw=0.98,          # Эталон для пленочной эстетики, гор, пыли и брутализма
            photoreal_human=0.96,
            nsfw_photoreal=0.88,         # Контрастная, реалистичная влажная кожа и естественное тело
            nsfw_stylized=0.05,
            anime_2d=0.00,
            deep_focus_optics=0.95       # Отличная резкость дальних планов без каши
        ),
        canon=AuthorCanon(
            default_cfg=5.5,
            min_cfg=4.5,
            max_cfg=6.0,
            recommended_steps=28,
            sampler_name="DPM++ 2M",
            scheduler="Karras",
            clip_skip=1,
            allow_adetailer=True,
            default_ad_denoise=0.36,
            ad_prompt="detailed face, beautiful detailed eyes, natural skin texture, soft lighting",
            ad_negative="(deformed iris, deformed pupils:1.4), deformed face, bad eyes, poorly drawn eyes",
            base_negative=(
                "deformed iris, deformed pupils, (semi-realistic, cgi, 3d, render, sketch, cartoon, drawing, anime:1.4), "
                "text, close up, headshot, portrait, cropped, out of frame, cut off feet, feet out of frame, worst quality, "
                "low quality, jpeg artifacts, ugly, duplicate, morbid, mutilated, extra fingers, mutated hands, "
                "poorly drawn hands, poorly drawn face, mutation, deformed, blurry, dehydrated, bad anatomy, "
                "bad proportions, extra limbs, cloned face, disfigured, gross proportions, malformed limbs, "
                "missing arms, missing legs, extra arms, extra legs, fused fingers, too many fingers, long neck"
            ),
            prompt_prefix="",
            hr_upscaler="R-ESRGAN 4x+",
            default_hr_scale=1.25,
            default_hr_denoise=0.34,
            hr_second_pass_steps=12
        ),
        filename_aliases=("realisticvision", "rv60", "rv51", "realistic_vision")
    ),

    "cyberrealistic": ModelProfile(
        id="cyberrealistic",
        title="CyberRealistic Classic",
        description="Студийный свет, бальные платья, сложная драпировка, глянец, косплей, будуарное ню.",
        canonical_filename="CyberRealistic_V4.1_FP16.safetensors",
        capabilities=ModelCapabilities(
            surrealism_vfx=0.25,
            monsters_creatures=0.15,
            hard_surface_tech=0.82,
            complex_fabrics=0.98,        # Эталон для бальных платьев, кружева, корсетов и шелка
            cinematic_raw=0.82,
            photoreal_human=0.98,        # Микротекстура кожи и правильные пропорции лица
            nsfw_photoreal=0.98,         # Мировой эталон фотореалистичного студийного NSFW
            nsfw_stylized=0.10,
            anime_2d=0.00,
            deep_focus_optics=0.80
        ),
        canon=AuthorCanon(
            default_cfg=5.0,             # Защита от пережигания и пластикового блеска кожи
            min_cfg=4.0,
            max_cfg=5.5,
            recommended_steps=28,
            sampler_name="DPM++ 2M",
            scheduler="Karras",
            clip_skip=1,
            allow_adetailer=True,
            default_ad_denoise=0.34,
            ad_prompt="detailed face, sharp focused eyes, natural skin texture, studio rim lighting",
            ad_negative="(deformed iris, deformed pupils:1.3), deformed face, bad eyes, poorly drawn eyes",
            base_negative=(
                "(worst quality, low quality:1.4), deformed iris, deformed pupils, "
                "(semi-realistic, cgi, 3d, render, sketch, cartoon, drawing, anime:1.3), "
                "close up, headshot, portrait, cropped, out of frame, cut off feet, feet out of frame, "
                "text, jpeg artifacts, ugly, duplicate, morbid, mutilated, extra fingers, mutated hands, "
                "poorly drawn hands, poorly drawn face, bad anatomy, bad proportions, extra limbs, cloned face, "
                "disfigured, malformed limbs, missing legs"
            ),
            prompt_prefix="",
            hr_upscaler="R-ESRGAN 4x+",
            default_hr_scale=1.25,
            default_hr_denoise=0.34,
            hr_second_pass_steps=12
        ),
        filename_aliases=("cyberrealistic", "cyber", "cyberdelia")
    ),

    "artistic": ModelProfile(
        id="artistic",
        title="DreamShaper 8",
        description="Концептуальная пластичность, светящиеся глаза, магия, монстры, 2.5D фэнтези, пин-ап.",
        canonical_filename="dreamshaper_8.safetensors",
        capabilities=ModelCapabilities(
            surrealism_vfx=0.98,         # Непревзойденное понимание светящихся глаз, магии и аур
            monsters_creatures=0.95,     # Демоны, мутанты, рога, крылья без превращения в грязь
            hard_surface_tech=0.75,      # Стимпанк, ретрофутуризм, концепт-кары
            complex_fabrics=0.82,
            cinematic_raw=0.60,
            photoreal_human=0.65,        # 2.5D semi-realistic эстетика
            nsfw_photoreal=0.45,
            nsfw_stylized=0.95,          # Чувственный фэнтезийный пин-ап, суккубы, эльфийки
            anime_2d=0.55,
            deep_focus_optics=0.75
        ),
        canon=AuthorCanon(
            default_cfg=6.0,
            min_cfg=5.5,
            max_cfg=7.0,
            recommended_steps=25,
            sampler_name="DPM++ 2M",
            scheduler="Karras",
            clip_skip=1,
            allow_adetailer=True,
            default_ad_denoise=0.28,
            ad_prompt="",
            ad_negative="",
            base_negative="ugly, deformed, noisy, blurry, low contrast",
            prompt_prefix="",
            hr_upscaler="R-ESRGAN 4x+",
            default_hr_scale=1.25,
            default_hr_denoise=0.35,
            hr_second_pass_steps=12
        ),
        filename_aliases=("dreamshaper", "dreamshaper_8", "ds8", "fantasy")
    ),

    "anime": ModelProfile(
        id="anime",
        title="MeinaHentai v5",
        description="Чистый 2D аниме стиль, манга, хентай, вайфу, селл-шейдинг.",
        canonical_filename="meinahentai_v5.safetensors",
        capabilities=ModelCapabilities(
            surrealism_vfx=0.88,
            monsters_creatures=0.70,
            hard_surface_tech=0.40,
            complex_fabrics=0.75,
            cinematic_raw=0.05,
            photoreal_human=0.05,
            nsfw_photoreal=0.05,
            nsfw_stylized=0.99,          # Идеальный 2D NSFW/хентай
            anime_2d=0.99,               # Чистый 2D стиль
            deep_focus_optics=0.65
        ),
        canon=AuthorCanon(
            default_cfg=7.0,
            min_cfg=6.0,
            max_cfg=7.5,
            recommended_steps=24,
            sampler_name="Euler a",
            scheduler="Karras",
            clip_skip=2,
            allow_adetailer=False,       # ADetailer разрушает 2D аниме рисовку
            default_ad_denoise=0.0,
            ad_prompt="",
            ad_negative="",
            base_negative=(
                "(worst quality, low quality:1.4), (normal quality:1.2), lowres, "
                "(bad anatomy, bad hands, mutated hands, missing fingers, extra fingers:1.2), "
                "(realistic, photorealistic, 3d, cgi, render, semi-realistic:1.4), "
                "cropped, jpeg artifacts, watermark, signature, text"
            ),
            prompt_prefix="",
            hr_upscaler="R-ESRGAN 4x+ Anime6B",
            default_hr_scale=1.25,
            default_hr_denoise=0.32,
            hr_second_pass_steps=10
        ),
        filename_aliases=("meina", "meinahentai", "meinav5", "anime")
    )
}


# ── СОВМЕСТИМОСТЬ СО СТАРЫМ API ДЛЯ ДИАЛОГОВ И МЕНЮ ──────────────────────────

FORGE_MODELS: Dict[str, Dict[str, Any]] = {
    prof.id: {
        "pattern": prof.canonical_filename,
        "title": prof.title,
        "description": prof.description,
        "default_negative": prof.canon.base_negative
    }
    for prof in MODEL_REGISTRY.values()
}

STYLE_PROMPT_PREFIXES: Dict[str, str] = {
    prof.id: prof.canon.prompt_prefix for prof in MODEL_REGISTRY.values()
}

CORE_NEGATIVE = "bad anatomy, poorly drawn, deformed, extra limbs, missing limbs, bad hands, blurry"
CORE_NEGATIVE_PROMPT = CORE_NEGATIVE
STYLE_COMMON_NEGATIVES = CORE_NEGATIVE


def sanitize_prompt(prompt: str) -> str:
    """Очищает промпт от дубликатов тегов, сохраняя исходный порядок."""
    if not prompt:
        return ""
    seen = set()
    cleaned_tokens = []
    for item in prompt.split(","):
        token = item.strip()
        if token and token.lower() not in seen:
            seen.add(token.lower())
            cleaned_tokens.append(token)
    return ", ".join(cleaned_tokens)


def build_composite_negative(
    llm_negative: Optional[str] = None,
    model_negative: Optional[str] = None,
    core_negative: str = CORE_NEGATIVE_PROMPT
) -> str:
    raw_negative = ", ".join(filter(None, [
        (llm_negative or "").strip(),
        (model_negative or "").strip(),
        (core_negative or "").strip()
    ]))
    return sanitize_prompt(raw_negative)


def get_model_spec(style_key: str) -> Dict[str, Any]:
    return FORGE_MODELS.get(style_key.lower(), FORGE_MODELS["artistic"])


def find_checkpoint_for_style(style_key: str, available_models: List[Dict[str, Any]]) -> Optional[str]:
    """Универсальное и надежное сопоставление модели Forge с защитой от пересечений подстрок."""
    if not available_models:
        return None

    target_profile = MODEL_REGISTRY.get(style_key.lower())
    if not target_profile:
        return None

    canon_file = target_profile.canonical_filename.lower()
    # 1. Приоритетный поиск по точному имени файла чекпоинта
    for m in available_models:
        title = str(m.get("title", "")).lower()
        name = str(m.get("model_name", "")).lower()
        filename = str(m.get("filename", "")).lower()
        if canon_file == filename or canon_file in title or canon_file in name:
            return m.get("title") or m.get("model_name")

    # 2. Поиск по строгим алиасам с исключением перекрестных совпадений
    for m in available_models:
        title = str(m.get("title", "")).lower()
        name = str(m.get("model_name", "")).lower()
        filename = str(m.get("filename", "")).lower()
        combined = f"{title} {name} {filename}"

        # Защита от перехвата: Realistic Vision ни при каких условиях не сопоставляется с файлами CyberRealistic
        if target_profile.id == "realistic" and "cyber" in combined:
            continue

        for alias in target_profile.filename_aliases:
            if alias in combined:
                return m.get("title") or m.get("model_name")

    return None


def detect_style_from_checkpoint(checkpoint_name: str) -> str:
    """Определяет ID профиля по имени чекпоинта."""
    low = (checkpoint_name or "").lower()
    for profile in MODEL_REGISTRY.values():
        if profile.canonical_filename.lower() in low:
            return profile.id
        if any(alias in low for alias in profile.filename_aliases):
            return profile.id
    return "realistic"


def sync_registry_with_user_config() -> None:
    """
    Синхронизирует MODEL_REGISTRY с пользовательскими правками из art_director_models.json,
    если пользователь переназначил имена файлов чекпоинтов в ModelsManagerDialog.
    """
    try:
        from services.art_director_config import load_art_director_models
        user_models = load_art_director_models()
        for um in user_models:
            m_id = um.get("id", "").lower()
            ckpt = um.get("checkpoint", "").strip()
            if not ckpt:
                continue
            for pid, profile in list(MODEL_REGISTRY.items()):
                if pid == m_id or m_id in profile.filename_aliases or profile.canonical_filename.lower() in ckpt.lower():
                    updated_profile = ModelProfile(
                        id=profile.id,
                        title=um.get("name") or profile.title,
                        description=um.get("description") or profile.description,
                        canonical_filename=ckpt,
                        capabilities=profile.capabilities,
                        canon=profile.canon,
                        filename_aliases=profile.filename_aliases
                    )
                    MODEL_REGISTRY[pid] = updated_profile
                    if pid in FORGE_MODELS:
                        FORGE_MODELS[pid]["pattern"] = ckpt
                        FORGE_MODELS[pid]["title"] = updated_profile.title
                    break
    except Exception as e:
        import logging
        logging.getLogger("NeonRoute.ForgeModels").debug(f"Config sync skipped: {e}")

sync_registry_with_user_config()