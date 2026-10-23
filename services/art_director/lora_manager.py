"""
LoraManager: Safe CPU Metadata & Trigger Phrase Extractor for LoRA Weights in NeonRoute.
Reads binary header of .safetensors (0 MB VRAM) to automatically harvest top-3 trigger words.
"""

import json
import struct
import re
import logging
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from config import (
    FORGE_ROOT_DIR,
    FORGE_LORA_DIRS,
    DEFAULT_FORGE_LORA_DIR,
    LORA_REGISTRY_PATH,
    BASE_DIR
)

logger = logging.getLogger("NeonRoute.LoraManager")


def extract_lora_metadata(file_path: Path) -> List[str]:
    """Быстро считывает первые байты safetensors и извлекает до 3 ключевых триггеров (0 MB VRAM)."""
    try:
        with open(file_path, "rb") as f:
            header_size_bytes = f.read(8)
            if len(header_size_bytes) < 8:
                return []
            header_len = struct.unpack("<Q", header_size_bytes)[0]
            if header_len > 5 * 1024 * 1024 or header_len <= 0:
                return []
            header_json = f.read(header_len).decode("utf-8", errors="ignore")
            metadata = json.loads(header_json).get("__metadata__", {})

        triggers: List[str] = []
        for key in ("modelspec.trigger_phrase", "trained_words", "ss_tag_frequency"):
            val = metadata.get(key)
            if not val:
                continue
            if key == "ss_tag_frequency":
                freq_data = json.loads(val) if isinstance(val, str) else val
                tag_counts: Dict[str, int] = {}
                for dataset in freq_data.values():
                    if isinstance(dataset, dict):
                        for tag, cnt in dataset.items():
                            tag_counts[tag] = tag_counts.get(tag, 0) + cnt
                sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
                ignore = {"1girl", "1boy", "solo", "looking at viewer", "masterpiece", "highres", "breasts"}
                triggers.extend([t for t, _ in sorted_tags if t.lower() not in ignore][:3])
            else:
                raw_words = val if isinstance(val, list) else str(val).split(",")
                for w in raw_words:
                    clean_w = re.sub(r'[\[\]"\'`]', '', str(w)).strip()
                    if clean_w:
                        triggers.append(clean_w)

        seen = set()
        unique_triggers = []
        for t in triggers:
            tl = t.lower()
            if tl not in seen:
                seen.add(tl)
                unique_triggers.append(t)
        return unique_triggers[:3]
    except Exception:
        return []


def _ensure_lora_registry_path() -> Path:
    return LORA_REGISTRY_PATH


class LoraManager:
    _trigger_cache: Dict[str, List[str]] = {}

    def __init__(self):
        self._trigger_cache = LoraManager._trigger_cache

    @classmethod
    def get_triggers(cls, lora_name: str) -> List[str]:
        return cls._trigger_cache.get(lora_name.lower().strip(), [])

    @classmethod
    def build_lora_tag(cls, name: str, weight: float, prompt: str = "") -> str:
        """
        Собирает <lora:name:weight> и дописывает недостающие триггеры,
        если их еще нет в промпте: <lora:name:weight>, trigger1, trigger2.
        """
        tag = f"<lora:{name}:{weight}>"
        triggers = cls.get_triggers(name)
        low_p = prompt.lower() if prompt else ""
        missing = [t for t in triggers if t.lower() not in low_p]
        if missing:
            return f"{tag}, {', '.join(missing)}"
        return tag

    @classmethod
    def get_available_lora_stems(cls, lora_dir: Optional[Path] = None) -> List[str]:
        search_dirs = []
        if lora_dir:
            search_dirs.append(Path(lora_dir))
        else:
            search_dirs.extend([
                DEFAULT_FORGE_LORA_DIR,
                FORGE_ROOT_DIR / "webui" / "models" / "Lora",
                FORGE_ROOT_DIR / "models" / "Lora",
                FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "models" / "Lora"
            ])

        valid_exts = {".safetensors", ".pt", ".pth"}
        stems = set()
        for d in search_dirs:
            if d.exists() and d.is_dir():
                try:
                    for f in d.rglob("*"):
                        if f.is_file() and f.suffix.lower() in valid_exts:
                            lora_name = f.stem.lower()
                            stems.add(lora_name)
                            if f.suffix.lower() == ".safetensors" and lora_name not in cls._trigger_cache:
                                cls._trigger_cache[lora_name] = extract_lora_metadata(f)
                except Exception:
                    pass
        return list(stems)

    def scan_directory(self, lora_dir: Optional[Path] = None) -> List[str]:
        search_dirs = [Path(lora_dir)] if lora_dir else [
            DEFAULT_FORGE_LORA_DIR,
            FORGE_ROOT_DIR / "webui" / "models" / "Lora",
            FORGE_ROOT_DIR / "models" / "Lora",
            FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "models" / "Lora"
        ]
        valid_exts = {".safetensors", ".pt", ".pth"}
        stems = set()
        for d in search_dirs:
            if d.exists() and d.is_dir():
                try:
                    for f in d.rglob("*"):
                        if f.is_file() and f.suffix.lower() in valid_exts:
                            lora_name = f.stem.lower()
                            stems.add(lora_name)
                            if f.suffix.lower() == ".safetensors" and lora_name not in self._trigger_cache:
                                self._trigger_cache[lora_name] = extract_lora_metadata(f)
                except Exception:
                    pass
        return list(stems)

    @staticmethod
    def load_registry() -> Dict[str, Any]:
        reg_path = _ensure_lora_registry_path()
        if not reg_path.exists():
            return {"characters": {}, "utility": {}}
        try:
            with open(reg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Ошибка загрузки lora_registry.json: {e}")
            return {"characters": {}, "utility": {}}

    @classmethod
    def resolve_loras(
        cls,
        prompt: str,
        character_name: Optional[str] = None,
        style_id: str = "realistic",
        lora_dir: Optional[Path] = None
    ) -> Tuple[List[str], List[str]]:
        """
        Возвращает:
        (lora_tags, activation_tags)
        Пример: (['<lora:ciri:0.8>, ciri, scar', '<lora:add_detail:0.35>'], ['ciri, white hair, scar'])
        """
        registry = cls.load_registry()
        available_stems = cls.get_available_lora_stems(lora_dir)

        selected_lora_tags: List[str] = []
        activation_tags: List[str] = []

        low_prompt = prompt.lower()

        # 1. Поиск явно указанных LoRA в промпте (пользовательские теги)
        explicit_loras = re.findall(r'<lora:([^:>]+)', prompt, flags=re.IGNORECASE)
        if explicit_loras:
            chars_registry = registry.get("characters", {})
            for explicit_stem in explicit_loras:
                clean_stem = explicit_stem.strip().lower()
                matched_conf = None
                for c_key, conf in chars_registry.items():
                    conf_fname = str(conf.get("file_name", c_key)).strip().lower()
                    if conf_fname == clean_stem or c_key.lower() == clean_stem:
                        matched_conf = conf
                        break

                if matched_conf:
                    act = matched_conf.get("activation_tags", "").strip()
                    if act and act.lower() not in low_prompt:
                        activation_tags.append(act)
                        logger.info(f"[LoraManager] Подтянуты теги активации для явной LoRA '{clean_stem}': {act}")

                # Подтягивание триггеров из заголовка .safetensors
                meta_triggers = cls.get_triggers(clean_stem)
                for trg in meta_triggers:
                    if trg.lower() not in low_prompt and trg not in activation_tags:
                        activation_tags.append(trg)
                        logger.info(f"[LoraManager] Подтянут мета-триггер safetensors для '{clean_stem}': {trg}")

        # 2. Персонажная LoRA (если передано имя персонажа)
        if character_name:
            c_key = character_name.strip().lower().replace(" ", "_")
            chars_registry = registry.get("characters", {})
            char_conf = chars_registry.get(c_key)
            if not char_conf:
                for c_k, conf in chars_registry.items():
                    if conf.get("character", "").lower() == character_name.strip().lower():
                        char_conf = conf
                        break
            if char_conf:
                fname = char_conf.get("file_name", c_key)
                w = char_conf.get("weight", 0.8)
                if not any(fname.lower() in tag.lower() for tag in selected_lora_tags) and not any(fname.lower() in p.lower() for p in explicit_loras):
                    selected_lora_tags.append(cls.build_lora_tag(fname, w, prompt))
                    act = char_conf.get("activation_tags", "").strip()
                    if act and act.lower() not in low_prompt:
                        activation_tags.append(act)

        # 3. Сервисная LoRA микродетализации (add_detail)
        util_conf = registry.get("utility", {}).get("add_detail", {})
        allowed_styles = util_conf.get("allowed_styles", ["realistic", "cyberrealistic", "artistic"])

        if style_id in allowed_styles:
            fname = util_conf.get("file_name", "add_detail")
            w = util_conf.get("weight", 0.35)
            if available_stems and any(fname.lower() in s for s in available_stems):
                if not any(fname.lower() in tag.lower() for tag in selected_lora_tags):
                    tag_with_triggers = cls.build_lora_tag(fname, w, prompt)
                    selected_lora_tags.append(tag_with_triggers)
                    logger.info(f"[LoraManager] Подключена сервисная LoRA: {tag_with_triggers}")

        return selected_lora_tags, activation_tags

    @classmethod
    def register_character_lora(
        cls,
        character_name: str,
        file_stem: str,
        activation_tags: List[str],
        weight: float = 0.8
    ) -> bool:
        """Регистрирует или обновляет запись о персонажной LoRA в lora_registry.json."""
        try:
            reg_path = _ensure_lora_registry_path()
            reg_path.parent.mkdir(parents=True, exist_ok=True)
            registry = cls.load_registry()
            chars = registry.setdefault("characters", {})

            clean_name = character_name.strip()
            c_key = clean_name.lower().replace(" ", "_")

            triggers = list(dict.fromkeys([clean_name.lower(), file_stem.lower()] + [t.lower().strip() for t in activation_tags if t.strip()]))
            act_str = ", ".join(activation_tags).strip()

            chars[c_key] = {
                "character": clean_name,
                "file_name": file_stem,
                "triggers": triggers,
                "weight": weight,
                "activation_tags": act_str
            }

            from utils.file_utils import atomic_write_json
            if not atomic_write_json(reg_path, registry):
                return False

            logger.info(f"[LoraManager] LoRA '{file_stem}' успешно зарегистрирована для персонажа '{clean_name}'.")
            return True
        except Exception as e:
            logger.error(f"[LoraManager] Ошибка регистрации LoRA: {e}")
            return False
