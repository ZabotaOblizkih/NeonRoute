SDXL_MARKERS = ("xl", "sdxl", "pony", "dmd", "insta", "mop")


def is_sdxl_checkpoint(model_name: str) -> bool:
    """
    Определяет, относится ли чекпоинт к архитектуре SDXL.
    Проверяет маркеры в имени файла: 'xl', 'sdxl', 'pony', 'dmd', 'insta', 'mop' (без учета регистра)
    либо сверяет размер/метаданные, если модель присутствует в реестре.
    """
    if not model_name:
        return False
    name_str = str(model_name).strip()
    if not name_str:
        return False
    name_lower = name_str.lower()

    if any(m in name_lower for m in SDXL_MARKERS):
        return True

    try:
        from services.art_director_config import load_art_director_models
        for m in load_art_director_models():
            ckpt = str(m.get("checkpoint", "")).lower()
            m_id = str(m.get("id", "")).lower()
            m_name = str(m.get("name", "")).lower()
            if name_lower in (ckpt, m_id, m_name) or (ckpt and ckpt in name_lower):
                arch = str(m.get("architecture", "")).lower()
                base_model = str(m.get("base_model", "")).lower()
                if "sdxl" in arch or "sdxl" in base_model or "pony" in arch:
                    return True
                if any(m_marker in ckpt for m_marker in SDXL_MARKERS):
                    return True
                size_gb = m.get("size_gb") or m.get("size")
                if size_gb and isinstance(size_gb, (int, float)) and size_gb > 5.0:
                    return True
    except Exception:
        pass

    return False

from dataclasses import dataclass
from typing import Optional, Dict, Any, List

@dataclass
class GenerationPlan:
    style: str = "realistic"
    target_model_key: str = "realistic"
    model_title: str = "Realistic"
    target_checkpoint: str = ""
    positive_prompt: str = ""
    negative_prompt: str = ""
    width: int = 512
    height: int = 768
    steps: int = 25
    cfg_scale: float = 7.0
    sampler_name: str = "DPM++ 2M"
    scheduler: str = "Karras"
    enable_hr: bool = False
    hr_scale: float = 1.5
    hr_upscaler: str = "R-ESRGAN 4x+"
    denoising_strength: float = 0.35
    hr_second_pass_steps: int = 15
    clip_skip: int = 1
    enable_adetailer: bool = True
    enable_hand_adetailer: bool = False
    prompt: Optional[str] = None
    ad_denoising_strength: Optional[float] = None
    ad_prompt: Optional[str] = None
    ad_negative_prompt: Optional[str] = None
    ad_mask_blur: int = 4
    seed: int = -1
    subseed: int = -1
    hires_upscaler: str = "R-ESRGAN 4x+"
    hires_denoise: float = 0.35
    reasoning: str = ""
    style_badge: str = ""
    is_ambiguous: bool = False
    clarification_message: str = ""
    unknown_words: Optional[List[str]] = None
    adetailer_face_denoise: Optional[float] = None
    adetailer_hands: Optional[bool] = None
    adetailer_face_model: str = "face_yolov8m.pt"
    adetailer_face_confidence: float = 0.44
    adetailer_hands_enabled: bool = False
    adetailer_hands_model: str = "hand_yolov9c.pt"
    adetailer_hands_confidence: float = 0.38
    adetailer_hands_denoise: float = 0.40
    adetailer_face_inpaint_size: int = 512
    adetailer_inpaint_size: int = 768
    adetailer_hands_inpaint_size: int = 512
    adetailer_hands_mask_blur: int = 8
    adetailer_hands_prompt: Optional[str] = None
    adetailer_hands_negative_prompt: Optional[str] = None
    archetype: str = "unknown"
    raw_prompt: Optional[str] = None

    def __post_init__(self):
        if self.prompt and not self.positive_prompt:
            self.positive_prompt = self.prompt
        elif self.positive_prompt and not self.prompt:
            self.prompt = self.positive_prompt

    @classmethod
    def create_ambiguous(cls, message: str, unknown_words: Optional[List[str]] = None) -> 'GenerationPlan':
        return cls(
            style="ambiguous",
            target_model_key="none",
            model_title="Ambiguous",
            target_checkpoint="",
            positive_prompt="",
            negative_prompt="",
            width=512,
            height=512,
            steps=20,
            cfg_scale=6.0,
            sampler_name="DPM++ 2M",
            scheduler="Karras",
            enable_hr=False,
            hr_scale=1.0,
            hr_upscaler="None",
            denoising_strength=0.3,
            hr_second_pass_steps=10,
            clip_skip=1,
            enable_adetailer=False,
            enable_hand_adetailer=False,
            is_ambiguous=True,
            clarification_message=message,
            unknown_words=unknown_words or []
        )

    def to_forge_kwargs(self, output_dir: Optional[Any] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
        payload = {
            "prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "steps": self.steps,
            "width": self.width,
            "height": self.height,
            "cfg_scale": self.cfg_scale,
            "sampler_name": self.sampler_name,
            "scheduler": self.scheduler,
            "checkpoint": self.target_checkpoint,
            "seed": self.seed,
            "subseed": getattr(self, "subseed", -1),
            "clip_skip": self.clip_skip,
            "enable_hr": self.enable_hr,
            "hr_scale": self.hr_scale,
            "hr_upscaler": self.hr_upscaler or getattr(self, "hires_upscaler", "R-ESRGAN 4x+"),
            "denoising_strength": self.denoising_strength if self.denoising_strength is not None else getattr(self, "hires_denoise", 0.35),
            "hr_second_pass_steps": self.hr_second_pass_steps,
            "enable_adetailer": self.enable_adetailer,
            "enable_hand_adetailer": getattr(self, "adetailer_hands_enabled", False),
            "ad_denoising_strength": getattr(self, "adetailer_face_denoise", 0.32),
            "ad_prompt": self.prompt or self.positive_prompt or "",
            "ad_negative_prompt": self.negative_prompt or "",
            "ad_mask_blur": self.ad_mask_blur,
            "output_dir": output_dir,
            "session_id": session_id
        }

        alwayson = {}
        if getattr(self, "enable_adetailer", True):
            base_steps = max(1, int(getattr(self, "steps", 25) or 25))
            if base_steps <= 10:
                ad_face_steps = max(10, base_steps + 4)
                ad_hand_steps = max(12, base_steps + 6)
            else:
                ad_face_steps = min(16, max(10, int(base_steps * 0.55)))
                ad_hand_steps = min(16, max(12, int(base_steps * 0.55)))

            shared_ad_prompt = self.prompt or self.positive_prompt or ""
            shared_ad_negative = self.negative_prompt or ""

            face_model_name = getattr(self, "adetailer_face_model", "face_yolov8m.pt")
            face_inpaint_sz = int(
                getattr(self, "adetailer_face_inpaint_size", None)
                or getattr(self, "adetailer_inpaint_size", 512)
            )
            face_slot = {
                "ad_model": face_model_name,
                "ad_prompt": shared_ad_prompt,
                "ad_negative_prompt": shared_ad_negative,
                "ad_confidence": getattr(self, "adetailer_face_confidence", 0.44),
                "ad_denoising_strength": getattr(self, "adetailer_face_denoise", 0.35),
                "ad_use_steps": False,
                "ad_steps": ad_face_steps,
                "ad_use_cfg_scale": False,
                "ad_inpaint_only_masked": True,
                "ad_use_inpaint_width_height": True,
                "ad_inpaint_width": face_inpaint_sz,
                "ad_inpaint_height": face_inpaint_sz,
            }

            hands_active = bool(
                getattr(self, "adetailer_hands_enabled", False)
                or getattr(self, "adetailer_hands", False)
            )
            hand_model_name = getattr(self, "adetailer_hands_model", "hand_yolov9c.pt")
            hand_sz = getattr(self, "adetailer_hands_inpaint_size", 512)
            hand_slot = {
                "ad_model": hand_model_name,
                "ad_prompt": shared_ad_prompt,
                "ad_negative_prompt": shared_ad_negative,
                "ad_confidence": getattr(self, "adetailer_hands_confidence", 0.38),
                "ad_denoising_strength": getattr(self, "adetailer_hands_denoise", 0.40),
                "ad_use_steps": False,
                "ad_steps": ad_hand_steps,
                "ad_use_cfg_scale": False,
                "ad_mask_blur": getattr(self, "adetailer_hands_mask_blur", 8),
                "ad_inpaint_only_masked": True,
                "ad_use_inpaint_width_height": True,
                "ad_inpaint_width": hand_sz,
                "ad_inpaint_height": hand_sz,
            }

            ad_args = [
                True,        # ad_enable (Master)
                False,       # ad_skip_img2img
                face_slot,   # Slot 1: Face
            ]
            if hands_active:
                ad_args.append(hand_slot)

            alwayson["ADetailer"] = {"args": ad_args}

        payload["alwayson_scripts"] = alwayson
        return payload


# Alias for compatibility
RenderPlan = GenerationPlan
