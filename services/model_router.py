"""
Vector Model Router for NeonRoute AI Art Director.
Provides CPU-based semantic routing using FastEmbed (sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2).
Matches generation prompts with registered models based on cosine similarity with model routing descriptions.
"""

import re
import json
import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

from config import DATA_DIR

logger = logging.getLogger("NeonRoute.ModelRouter")

DEFAULT_DATA_PATH = DATA_DIR / "art_director_models.json"


def normalize_model_choice(choice: Optional[str]) -> Optional[str]:
    """
    Единый контракт нормализации выбора модели/стиля.
    Преобразует UI-заглушки авто-режима ('✨ Арт-директор (Авто)', 'default', 'auto', 'none', '') в None,
    разблокируя автоматическую маршрутизацию в ModelRouter.
    """
    if not choice or not isinstance(choice, str):
        return None
    clean = choice.strip()
    if not clean or clean.startswith("✨"):
        return None
    if clean.lower() in ("default", "default (авто)", "auto", "авто", "none", "null", "арт-директор (авто)"):
        return None
    return clean


DEFAULT_ROUTER_MODELS: List[Dict[str, Any]] = [
    {
        "id": "default_photoreal",
        "name": "Universal Photorealism (SD 1.5)",
        "checkpoint": "model_sd15.safetensors",
        "architecture": "sd15",
        "routing_description": "Повседневные фотореалистичные кадры, люди, реальная жизнь, естественный будуар, портреты, студийные фото, природа, пейзажи, raw photo, realistic, 35mm dslr photography, real human, portrait.",
        "llm_guidance": "Стиль: Фотореализм. Используй теги: RAW photo, 35mm photograph, authentic skin texture, cinematic lighting. Избегай слов: 3d render, cartoon, anime, illustration.",
        "is_default": True,
        "style_triggers": [],
        "active": True,
        "pipeline": {
            "sampler": "DPM++ SDE Karras",
            "steps": 10,
            "cfg_scale": 1.5,
            "clip_skip": 1,
            "hires_upscaler": "4x_NMKD-Superscale-SP_178000_G",
            "hires_denoise": 0.35,
            "negative_prompt": "(deformed iris, deformed pupils, semi-realistic, cgi, 3d, render, sketch, cartoon, drawing, anime, mutated hands and fingers:1.4), (deformed, distorted, disfigured:1.3), poorly drawn, bad anatomy, wrong anatomy, extra limb, missing limb, floating limbs, disconnected limbs, mutation, mutated, ugly, disgusting, amputation"
        }
    },
    {
        "id": "cyberrealistic",
        "name": "CyberRealistic Classic",
        "checkpoint": "CyberRealistic_V4.1_FP16.safetensors",
        "architecture": "sd15",
        "routing_description": "Технологичный фотореализм, киберпанк, глянец, латекс, броня, хай-тек будуар, контрастное неоновое освещение, роботы, киборги, сложные платья, шелк, корсеты, cyberpunk, sci-fi, neon city, high-tech, futuristic.",
        "llm_guidance": "Стиль: Студийный глянец и киберпанк. Используй теги: candid RAW photo, studio lighting, sharp focus, intricate fabrics, neon reflections. Избегай слов: 2d, sketch, flat colors.",
        "is_default": False,
        "style_triggers": [],
        "active": True,
        "pipeline": {
            "sampler": "DPM++ 2M Karras",
            "steps": 25,
            "cfg_scale": 6.0,
            "clip_skip": 1,
            "hires_upscaler": "4x-UltraSharp",
            "hires_denoise": 0.32,
            "negative_prompt": "worst quality, low quality, bad anatomy, bad proportions, deformed, badhandv4"
        }
    },
    {
        "id": "artistic",
        "name": "DreamShaper 8",
        "checkpoint": "dreamshaper_8.safetensors",
        "routing_description": "Художественные арты, цифровая живопись, магия, сказочные расы, фэнтези, концепт-арт, эльфы, драконы, волшебство, 2D мультфильмы, фигурки, игрушки, стилизация, fantasy art, digital painting, illustration, magic, spell, cartoon, figurine.",
        "llm_guidance": "Стиль: Художественный арт и стилизация. Для мультфильмов используй: 'flat 2d cartoon, cel-shaded, vibrant colors'. Для фигурок: 'macro photo of vinyl chibi figurine, plastic base'. Для фэнтези: 'digital concept art, magical glowing vfx'.",
        "is_default": False,
        "style_triggers": ["рисунок", "картина", "маслом", "акварель", "иллюстрация", "painting", "artstation"],
        "active": True,
        "pipeline": {
            "sampler": "DPM++ SDE Karras",
            "steps": 28,
            "cfg_scale": 7.0,
            "clip_skip": 2,
            "hires_upscaler": "Latent (bicubic antialiased)",
            "hires_denoise": 0.46,
            "negative_prompt": "BadDream, FastNegativeV2"
        }
    },
    {
        "id": "anime",
        "name": "MeinaHentai v5",
        "checkpoint": "meinahentai_v5.safetensors",
        "routing_description": "Чистая 2D аниме-рисовка, манга, вайфу, стилизованный аниме-ролеплей, хентай, селл-шейдинг, 2D anime style, manga, waifu, anime girl, cel shaded, 2d, illustration.",
        "llm_guidance": "Стиль: Чистый 2D аниме. Используй теги: anime aesthetic, clean lineart, cel shading, vibrant anime colors, 2d illustration. Избегай слов: photorealistic, 3d, realistic skin.",
        "is_default": False,
        "style_triggers": ["аниме", "anime", "манга", "manga", "2d", "2д", "вайфу", "тян", "селл-шейдинг"],
        "active": True,
        "pipeline": {
            "sampler": "DPM++ SDE Karras",
            "steps": 25,
            "cfg_scale": 7.0,
            "clip_skip": 2,
            "hires_upscaler": "R-ESRGAN 4x+ Anime6B",
            "hires_denoise": 0.4,
            "negative_prompt": "worst quality, low quality, bad anatomy, easynegative"
        }
    }
]


class ModelRouter:
    """
    Semantic model router using FastEmbed on CPU.
    Computes cosine similarity between generation prompts and routing descriptions.
    """

    _instance: Optional["ModelRouter"] = None

    VISUAL_TRIGGERS = (
        "нарисуй", "сгенерируй", "изобрази", "отрисуй", "покажи", "создай арт",
        "сделай арт", "визуализируй", "иллюстрац", "draw", "paint", "image",
        "art", "!art", "!арт", "!img", "портрет", "образ", "картинк", "рисунок"
    )

    def __new__(cls, data_path: Optional[Path] = None, auto_load: bool = True):
        if data_path is None and cls._instance is not None:
            return cls._instance
        instance = super().__new__(cls)
        if data_path is None:
            cls._instance = instance
        return instance

    @classmethod
    def get_instance(cls, data_path: Optional[Path] = None, auto_load: bool = True) -> "ModelRouter":
        """Returns the singleton instance of ModelRouter, instantiating it if not yet created."""
        if cls._instance is None:
            cls._instance = cls(data_path=data_path, auto_load=auto_load)
        return cls._instance

    def __init__(self, data_path: Optional[Path] = None, auto_load: bool = True):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.data_path = Path(data_path) if data_path else DEFAULT_DATA_PATH
        self.profiles: List[Dict[str, Any]] = []
        self.embeddings: Dict[str, np.ndarray] = {}
        self._embed_engine = None

        if auto_load:
            self.load_profiles()
            self.update_embeddings_async()

    def update_embeddings_async(self, delay: float = 5.0):
        """
        Runs update_embeddings in a background daemon thread with deferred delay
        to eliminate CPU contention with LLM startup and prevent GUI freeze.
        """
        if delay > 0:
            timer = threading.Timer(delay, self.update_embeddings)
            timer.daemon = True
            timer.name = "ModelRouterDeferredWarmup"
            timer.start()
            return timer
        thread = threading.Thread(target=self.update_embeddings, daemon=True, name="ModelRouterWarmup")
        thread.start()
        return thread

    def get_embedding_engine(self):
        """
        Lazily acquires the FastEmbed TextEmbedding engine running on CPU ONNX.
        Reuses engine singleton from services.memory_db to avoid duplication.
        """
        if self._embed_engine is not None:
            return self._embed_engine

        try:
            from services.memory_db import _get_embedding_engine
            engine, _, _ = _get_embedding_engine()
            if engine:
                self._embed_engine = engine
                return self._embed_engine
        except Exception as e:
            logger.error(f"[ModelRouter] Failed to acquire FastEmbed TextEmbedding from memory_db: {e}")
        return None

    def load_profiles(self) -> List[Dict[str, Any]]:
        """
        Loads model profiles strictly from data/art_director_models.json.
        Legacy config/ directory lookup completely removed.
        """
        self.data_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.data_path.exists():
            self.profiles = [dict(m) for m in DEFAULT_ROUTER_MODELS]
            self.save_profiles(self.profiles)
            return self.profiles

        try:
            with open(self.data_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            models = data.get("models")
            if isinstance(models, list) and models:
                self.profiles = [dict(m) for m in models]
                for m in self.profiles:
                    if not m.get("architecture"):
                        name_and_ckpt = f"{m.get('name', '')} {m.get('checkpoint', '')}".lower()
                        m["architecture"] = "sdxl" if any(x in name_and_ckpt for x in ("xl", "pony", "illustrious")) else "sd15"
                return self.profiles
            else:
                self.profiles = [dict(m) for m in DEFAULT_ROUTER_MODELS]
                self.save_profiles(self.profiles)
                return self.profiles
        except Exception as e:
            logger.error(f"[ModelRouter] Failed to load {self.data_path}: {e}")
            self.profiles = [dict(m) for m in DEFAULT_ROUTER_MODELS]
            return self.profiles

    def save_profiles(self, profiles: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        Saves model profiles atomically to data/art_director_models.json.
        Ensures is_default (bool) and style_triggers (list[str]) are correctly preserved.
        If any model is marked is_default=True, resets is_default to False for all other models.
        """
        if profiles is not None:
            self.profiles = profiles

        # Ensure single is_default flag and normalized style_triggers
        default_found = False
        for p in self.profiles:
            triggers = p.get("style_triggers", [])
            if isinstance(triggers, str):
                triggers = [t.strip() for t in triggers.split(",") if t.strip()]
            elif isinstance(triggers, list):
                triggers = [str(t).strip() for t in triggers if str(t).strip()]
            else:
                triggers = []
            p["style_triggers"] = triggers

            if bool(p.get("is_default", False)):
                if not default_found:
                    p["is_default"] = True
                    default_found = True
                else:
                    p["is_default"] = False
            else:
                p["is_default"] = False

        data = {"models": self.profiles}
        from utils.file_utils import atomic_write_json
        success = atomic_write_json(self.data_path, data)
        if success:
            logger.info(f"[ModelRouter] Saved {len(self.profiles)} model profiles to {self.data_path}")
        return success

    def update_profile(self, profile_id: str, updates: Dict[str, Any]) -> bool:
        """
        Updates a model profile by ID and atomically saves to JSON.
        If updates contain is_default=True, resets is_default on all other profiles.
        """
        found = False
        set_default = bool(updates.get("is_default", False))
        for p in self.profiles:
            if p.get("id") == profile_id:
                p.update(updates)
                found = True
            elif set_default:
                p["is_default"] = False

        if found:
            self.save_profiles(self.profiles)
        return found

    def update_embeddings(self) -> None:
        """
        Calculates FastEmbed embeddings for the routing_description field of each active model.
        """
        self.load_profiles()
        engine = self.get_embedding_engine()
        if engine is None:
            logger.warning("[ModelRouter] Embedding engine unavailable, skipping update_embeddings.")
            return

        active_models = [m for m in self.profiles if m.get("active", True)]
        new_embeddings: Dict[str, np.ndarray] = {}

        for m in active_models:
            m_id = m.get("id", "")
            desc = (m.get("routing_description") or m.get("description") or "").strip()
            if not m_id or not desc:
                continue

            try:
                vectors = list(engine.embed([desc]))
                if vectors:
                    vec = np.array(vectors[0], dtype=np.float32)
                    norm = np.linalg.norm(vec)
                    if norm > 1e-9:
                        vec = vec / norm
                    new_embeddings[m_id] = vec
            except Exception as e:
                logger.error(f"[ModelRouter] Error embedding profile '{m_id}': {e}")

        self.embeddings = new_embeddings
        self._embeddings_loaded = True
        logger.info(f"[ModelRouter] Vector embeddings updated for {len(self.embeddings)} active models.")

    def match_prompt_detailed(self, prompt: str, profiles: Optional[List[Dict[str, Any]]] = None) -> Tuple[Optional[str], float, str]:
        """
        Единая интеллектуальная гибридная маршрутизация (SSOT).
        Возвращает (winner_id, score, reason), где reason:
        - 'trigger': прямое совпадение со style_triggers (score = 0.95)
        - 'vector': уверенное семантическое сходство FastEmbed (score >= 0.35 с отрывом)
        - 'default': нейтральный запрос, выбрана модель по умолчанию (is_default=True)
        - 'fallback': первая доступная активная модель
        """
        p_lower = (prompt or "").lower().strip()
        target_profiles = [p for p in (profiles if profiles is not None else self.profiles) if p.get("active", True)]
        if not target_profiles:
            return None, 0.0, "none"

        # Шаг 1: Проверка явных триггеров стилей
        for prof in target_profiles:
            triggers = prof.get("style_triggers", [])
            if isinstance(triggers, str):
                triggers = [t.strip() for t in triggers.split(",") if t.strip()]

            for tr in triggers:
                tr_clean = tr.strip().lower()
                if not tr_clean:
                    continue

                if len(tr_clean) <= 3 or tr_clean.isascii():
                    pattern = rf"\b{re.escape(tr_clean)}\b"
                else:
                    pattern = rf"\b{re.escape(tr_clean)}"

                if re.search(pattern, p_lower):
                    return prof.get("id"), 0.95, "trigger"

        # Шаг 2: Векторный скоринг FastEmbed
        scores_map: Dict[str, float] = {}
        if profiles is not None:
            custom_ranked = self.rank_custom_models(prompt, target_profiles)
            for p_dict, sc in custom_ranked:
                scores_map[p_dict.get("id")] = sc
        else:
            ranked = self._compute_similarities(prompt)
            active_ids = {p.get("id") for p in target_profiles}
            scores_map = {m_id: sc for m_id, sc in ranked if m_id in active_ids}

        default_prof = next((p for p in target_profiles if p.get("is_default")), None)
        default_id = default_prof.get("id") if default_prof else None
        default_score = scores_map.get(default_id, 0.0) if default_id else 0.0

        if scores_map:
            best_id, best_score = max(scores_map.items(), key=lambda x: x[1])
            if best_id == default_id:
                return default_id, max(best_score, 0.85), "default"

            # Выраженное семантическое соответствие (порог >= 0.35 и отрыв > 0.05)
            if best_score >= 0.35 and (best_score > default_score + 0.05):
                return best_id, best_score, "vector"

        # Шаг 3: Нейтральный запрос -> модель по умолчанию
        if default_prof:
            return default_id, max(default_score, 0.85), "default"

        # Шаг 4: Фоллбэк
        fallback_id = target_profiles[0].get("id")
        return fallback_id, scores_map.get(fallback_id, 0.50), "fallback"

    def match_prompt(self, prompt: str, profiles: Optional[List[Dict[str, Any]]] = None) -> Tuple[Optional[str], float]:
        """Публичный контракт для генерации арта (обратная совместимость)."""
        winner_id, score, _ = self.match_prompt_detailed(prompt, profiles=profiles)
        return winner_id, score

    def _compute_similarities(self, prompt: str) -> List[Tuple[str, float]]:
        """
        Векторный скоринг FastEmbed для активных моделей.
        """
        return self.rank_models(prompt)

    def rank_models(self, prompt: str) -> List[Tuple[str, float]]:
        """
        Returns list of (model_id, score) for all active models sorted by similarity score descending.
        """
        if not self.embeddings and not getattr(self, "_embeddings_loaded", False):
            self.update_embeddings()
            self._embeddings_loaded = True

        if not self.embeddings or not prompt or not prompt.strip():
            return [(m_id, 0.0) for m_id in self.embeddings.keys()]

        engine = self.get_embedding_engine()
        if engine is None:
            return [(m_id, 0.0) for m_id in self.embeddings.keys()]

        try:
            vectors = list(engine.embed([prompt.strip()]))
            if not vectors:
                return []
            prompt_vec = np.array(vectors[0], dtype=np.float32)
            norm = np.linalg.norm(prompt_vec)
            if norm > 1e-9:
                prompt_vec = prompt_vec / norm

            results = []
            for m_id, m_vec in self.embeddings.items():
                score = float(np.dot(prompt_vec, m_vec))
                results.append((m_id, round(score, 4)))

            results.sort(key=lambda x: x[1], reverse=True)
            return results
        except Exception as e:
            logger.error(f"[ModelRouter] Error ranking models: {e}")
            return []

    def rank_custom_models(self, prompt: str, models: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], float]]:
        """
        Live evaluation helper for UI: takes current unsaved model list from UI,
        computes similarity for active items on-the-fly and returns sorted list.
        """
        if not prompt or not prompt.strip():
            return [(m, 0.0) for m in models if m.get("active", True)]

        engine = self.get_embedding_engine()
        if engine is None:
            return [(m, 0.0) for m in models if m.get("active", True)]

        active_models = [m for m in models if m.get("active", True) and (m.get("routing_description") or m.get("description", "")).strip()]
        if not active_models:
            return []

        try:
            texts = [prompt.strip()] + [(m.get("routing_description") or m.get("description", "")).strip() for m in active_models]
            vectors = list(engine.embed(texts))
            if len(vectors) < len(texts):
                return []

            p_vec = np.array(vectors[0], dtype=np.float32)
            p_norm = np.linalg.norm(p_vec)
            if p_norm > 1e-9:
                p_vec = p_vec / p_norm

            results = []
            for idx, model in enumerate(active_models):
                m_vec = np.array(vectors[idx + 1], dtype=np.float32)
                m_norm = np.linalg.norm(m_vec)
                if m_norm > 1e-9:
                    m_vec = m_vec / m_norm
                score = float(np.dot(p_vec, m_vec))
                results.append((model, round(score, 4)))

            results.sort(key=lambda x: x[1], reverse=True)
            return results
        except Exception as e:
            logger.error(f"[ModelRouter] Error ranking custom models: {e}")
            return []

    def get_profile(self, identifier: Optional[str]) -> Optional[Dict[str, Any]]:
        """Returns full model profile dict by ID, name, or checkpoint filename."""
        if not identifier:
            return None
        target = str(identifier).strip().lower()
        target_stem = Path(target).stem.lower()
        for m in self.profiles:
            m_id = m.get("id", "").lower()
            m_ckpt = m.get("checkpoint", "").lower()
            m_name = m.get("name", "").lower()
            if target in (m_id, m_ckpt, m_name) or target_stem == Path(m_ckpt).stem.lower():
                return m
        return None

    def get_checkpoint_for_model(self, model_id: str) -> Optional[str]:
        """Returns the Forge checkpoint filename configured for a given model ID or name."""
        if not model_id:
            return None
        target = str(model_id).strip().lower()
        for m in self.profiles:
            if m.get("id", "").lower() == target or m.get("name", "").lower() == target or m.get("checkpoint", "").lower() == target:
                return m.get("checkpoint")
        return None

    def get_default_profile(self) -> Optional[Dict[str, Any]]:
        """Возвращает универсальный дефолтный профиль активной модели."""
        for m in self.profiles:
            if m.get("is_default") and m.get("active", True) is not False:
                return m
        for m in self.profiles:
            if m.get("active", True) is not False:
                return m
        return self.profiles[0] if self.profiles else None

    def pre_route(self, text: str) -> Tuple[str, str]:
        """
        Быстрый семантический пре-роутинг перед ответом LLM на CPU (FastEmbed).
        Если запрос не содержит визуальных намерений, возвращает ('realistic', '')
        во избежание засорения обычного диалога.
        """
        clean_text = (text or "").strip()
        low_t = clean_text.lower()

        # Шлюз интента: не вмешиваемся в обычный диалог
        if not any(t in low_t for t in self.VISUAL_TRIGGERS):
            def_prof = self.get_default_profile()
            def_id = def_prof.get("id", "default") if def_prof else "default"
            return def_id, ""

        # Убираем сам глагол-триггер для более точного смыслового эмбеддинга темы
        content_prompt = re.sub(
            r'^(?:нарисуй|сгенерируй|покажи|изобрази|сделай|отрисуй|создай|draw|paint)\s+(?:мне|нам|пожалуйста\s+)?',
            '', clean_text, flags=re.IGNORECASE
        ).strip()

        target_prompt = content_prompt or clean_text
        best_id, score = self.match_prompt(target_prompt)

        # Извлекаем подсказку для LLM выбранной модели
        guidance = ""
        for m in self.profiles:
            if m.get("id") == best_id:
                raw_g = m.get("llm_guidance", "").strip()
                if raw_g:
                    guidance = f"<ArtGuidance>\n[Целевой стиль: {m.get('name', best_id)}]\n{raw_g}\n</ArtGuidance>"
                break

        logger.info(f"[ModelRouter.PreRoute] Запрос: '{clean_text[:40]}' -> Модель: {best_id} (score: {score:.3f})")
        return best_id, guidance

