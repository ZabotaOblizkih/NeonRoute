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
    adetailer_face_model: str = "face_yolov8n.pt"
    adetailer_face_confidence: float = 0.35
    adetailer_hands_enabled: bool = False
    adetailer_hands_model: str = "hand_yolov8n.pt"
    adetailer_hands_confidence: float = 0.40
    adetailer_hands_denoise: float = 0.22

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
            "ad_denoising_strength": getattr(self, "adetailer_face_denoise", 0.30),
            "ad_prompt": self.ad_prompt,
            "ad_negative_prompt": self.ad_negative_prompt,
            "ad_mask_blur": self.ad_mask_blur,
            "output_dir": output_dir,
            "session_id": session_id
        }

        alwayson = {}
        if getattr(self, "enable_adetailer", True):
            face_slot = {
                "ad_model": getattr(self, "adetailer_face_model", "face_yolov8n.pt"),
                "ad_confidence": getattr(self, "adetailer_face_confidence", 0.35),
                "ad_denoising_strength": getattr(self, "adetailer_face_denoise", 0.30),
                "ad_inpaint_only_masked": True
            }
            if getattr(self, "ad_prompt", None):
                face_slot["ad_prompt"] = self.ad_prompt
            if getattr(self, "ad_negative_prompt", None):
                face_slot["ad_negative_prompt"] = self.ad_negative_prompt

            hands_active = bool(getattr(self, "adetailer_hands_enabled", False) or getattr(self, "adetailer_hands", False))
            hand_slot = {
                "ad_model": getattr(self, "adetailer_hands_model", "hand_yolov8n.pt"),
                "ad_confidence": getattr(self, "adetailer_hands_confidence", 0.40),
                "ad_denoising_strength": getattr(self, "adetailer_hands_denoise", 0.22),
                "ad_inpaint_only_masked": True
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
