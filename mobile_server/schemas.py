"""
Pydantic v2 schemas for NeonRoute Mobile Server Gateway.
"""

from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ServiceStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    port: int
    is_online: bool
    name: str


class HardwareTelemetry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gpu_name: str
    vram_used_mb: float
    vram_total_mb: float
    vram_percent: float
    ram_used_mb: float
    ram_total_mb: float
    ram_percent: float


class SystemStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    services: List[ServiceStatus]
    telemetry: HardwareTelemetry
    active_mode: str


class SetModeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str


class SetModeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    active_mode: str
    message: str


class SessionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    mode: str
    updated_at: Optional[str] = None
    message_count: int = 0


class SessionDetailResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    mode: str
    messages: List[Dict[str, Any]]
    sampling_params: Dict[str, Any] = Field(default_factory=dict)
    character_data: Optional[Dict[str, Any]] = None


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str = "assistant"
    title: Optional[str] = None
    character_id: Optional[str] = None


class SessionMutationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    session_id: str
    message: str


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    text: str
    mode: str
    sampling_overrides: Optional[Dict[str, Any]] = None


class StreamTokenChunk(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: Literal["token", "thought", "thought_end", "done", "art_started", "error"]
    data: str


# ─── Art Director & SD Forge Subsystem Schemas ───

class ArtModelItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    is_default: bool = False
    triggers: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    checkpoint: Optional[str] = None
    is_active: bool = False


class ArtModelsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    models: List[ArtModelItem]
    active_model_id: str
    active_model: Optional[str] = None


class SelectArtModelRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: Optional[str] = None
    model_id: str


class GenerateArtRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    prompt: Optional[str] = ""
    negative_prompt: Optional[str] = ""
    aspect_ratio: str = "portrait"
    model_id: Optional[str] = None
    character_id: Optional[str] = None
    override_params: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    prompt_override: Optional[str] = None


class ArtProgressResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_generating: bool
    progress: float
    step: int
    total_steps: int
    eta_relative: float = 0.0
    preview_url: Optional[str] = None
    stage_name: Optional[str] = None


class GenerationParamsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model_name: str
    seed: int = -1
    steps: int = 28
    sampler: str = "Euler a"
    cfg_scale: float = 6.0
    denoising_strength: float = 0.35
    prompt: str = ""
    negative_prompt: str = ""
    image_path: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    hires_upscaler: Optional[str] = None
    enable_hr: Optional[bool] = None
    enable_adetailer: Optional[bool] = None

    @classmethod
    def from_plan(
        cls,
        plan: Any,
        forge_kwargs: Dict[str, Any],
        image_path: Any,
        fallback_model: str = "Auto",
        fallback_prompt: str = "",
        fallback_neg: str = "",
    ) -> "GenerationParamsPayload":
        return cls(
            model_name=getattr(plan, "model_title", None) or getattr(plan, "target_checkpoint", None) or fallback_model,
            seed=int(forge_kwargs.get("seed", -1)),
            steps=int(forge_kwargs.get("steps", 28)),
            sampler=str(forge_kwargs.get("sampler_name", "Euler a")),
            cfg_scale=float(forge_kwargs.get("cfg_scale", 6.0)),
            denoising_strength=float(forge_kwargs.get("denoising_strength", 0.35)),
            prompt=str(forge_kwargs.get("prompt", fallback_prompt)),
            negative_prompt=str(forge_kwargs.get("negative_prompt", fallback_neg)),
            image_path=str(image_path),
            width=forge_kwargs.get("width"),
            height=forge_kwargs.get("height"),
            hires_upscaler=forge_kwargs.get("hr_upscaler"),
            enable_hr=forge_kwargs.get("enable_hr"),
            enable_adetailer=forge_kwargs.get("enable_adetailer"),
        )


class DirectArtCommandRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    command: str


class GenerateArtResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    generation_params: Optional[GenerationParamsPayload] = None
    error: Optional[str] = None


class ArtGalleryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    filename: str
    url: str
    path: str
    session_id: Optional[str] = None
    timestamp: Optional[str] = None
    generation_params: Optional[Dict[str, Any]] = None


class ArtGalleryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: List[ArtGalleryItem]
    total: int


class SceneBackgroundResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    location: Optional[str] = None
    error: Optional[str] = None

