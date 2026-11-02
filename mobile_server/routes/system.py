"""
System routes for status checks, telemetry, and mode management.
"""

import asyncio
import psutil
from fastapi import APIRouter, Depends, HTTPException

from mobile_server.schemas import (
    SystemStatusResponse,
    ServiceStatus,
    HardwareTelemetry,
    SetModeRequest,
    SetModeResponse,
)
from mobile_server.dependencies import (
    get_health_checker,
    get_system_monitor,
    get_mode_coordinator,
    HealthChecker,
    SystemMonitor,
    HeadlessModeCoordinator,
)
from services.mode_coordinator import AppMode

router = APIRouter()


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(
    health_checker: HealthChecker = Depends(get_health_checker),
    system_monitor: SystemMonitor = Depends(get_system_monitor),
    coordinator: HeadlessModeCoordinator = Depends(get_mode_coordinator),
) -> SystemStatusResponse:
    """
    Returns the real-time health status of local services (LM Studio, Forge, TTS)
    and hardware telemetry (VRAM, RAM, GPU info).
    """
    health = await asyncio.to_thread(health_checker._perform_health_checks)
    stats = await asyncio.to_thread(system_monitor._poll_hardware)

    # Calculate exact RAM in MB
    try:
        v_mem = psutil.virtual_memory()
        ram_used_mb = round(v_mem.used / (1024 * 1024), 1)
        ram_total_mb = round(v_mem.total / (1024 * 1024), 1)
        ram_percent = float(v_mem.percent)
    except Exception:
        ram_used_mb = float(stats.get("ram_used_gb", 0.0)) * 1024
        ram_total_mb = float(stats.get("ram_total_gb", 16.0)) * 1024
        ram_percent = float(stats.get("ram_percent", 0.0))

    services = [
        ServiceStatus(
            port=1234,
            is_online=bool(health.get("lm_ok")),
            name="LM Studio (LLM)",
        ),
        ServiceStatus(
            port=7860,
            is_online=bool(health.get("forge_ok")),
            name="SD Forge",
        ),
        ServiceStatus(
            port=5010,
            is_online=bool(health.get("tts_ok")),
            name="F5-TTS",
        ),
    ]

    telemetry = HardwareTelemetry(
        gpu_name=str(stats.get("gpu_name") or "NVIDIA GPU"),
        vram_used_mb=float(stats.get("vram_used_mb", 0.0)),
        vram_total_mb=float(stats.get("vram_total_mb", 8192.0)),
        vram_percent=float(stats.get("vram_percent", 0.0)),
        ram_used_mb=ram_used_mb,
        ram_total_mb=ram_total_mb,
        ram_percent=ram_percent,
    )

    active_mode = coordinator.current_mode.value

    return SystemStatusResponse(
        services=services,
        telemetry=telemetry,
        active_mode=active_mode,
    )


@router.post("/mode", response_model=SetModeResponse)
async def set_active_mode(
    req: SetModeRequest,
    coordinator: HeadlessModeCoordinator = Depends(get_mode_coordinator),
) -> SetModeResponse:
    """
    Switches active operating mode (assistant, roleplay, engineer)
    via ModeCoordinator without GUI side effects.
    """
    mode_str = req.mode.strip().lower()
    valid_modes = {"assistant": AppMode.ASSISTANT, "roleplay": AppMode.ROLEPLAY, "engineer": AppMode.ENGINEER}

    if mode_str not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{req.mode}'. Allowed modes: {list(valid_modes.keys())}",
        )

    target_mode = valid_modes[mode_str]
    await asyncio.to_thread(coordinator.transition_to, target_mode)

    return SetModeResponse(
        success=True,
        active_mode=coordinator.current_mode.value,
        message=f"Active mode switched to {coordinator.current_mode.value}",
    )


@router.post("/unload-vram")
async def unload_vram():
    """
    Purges GPU memory by unloading LM Studio active models and Forge checkpoints.
    """
    details = {}

    def _unload_sync():
        # 1. Unload LM Studio
        try:
            from services.lm_studio import LMStudioClient
            LMStudioClient().unload_models_cli()
            details["lm_studio"] = "unloaded"
        except Exception as e:
            details["lm_studio"] = f"error: {e}"

        # 2. Unload Forge
        try:
            from services.forge_client import ForgeClient
            ForgeClient().unload_checkpoint()
            details["forge"] = "unloaded"
        except Exception as e:
            details["forge"] = f"error: {e}"

    await asyncio.to_thread(_unload_sync)
    return {
        "success": True,
        "message": "Память VRAM успешно очищена",
        "details": details,
    }

