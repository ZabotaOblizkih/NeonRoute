"""
services/assistant_flow.py
Service layer and adapter for Assistant Flow operations, including
cascading sampling parameter resolution and Gallery Art Pass dispatch.
"""
from typing import Dict, Any, Optional
import logging

try:
    from ui.controllers.assistant_flow_controller import (
        AssistantFlowControllerMixin,
        run_assistant_inference_deferred,
    )
except ImportError:
    AssistantFlowControllerMixin = None  # type: ignore
    run_assistant_inference_deferred = None  # type: ignore

logger = logging.getLogger(__name__)


def resolve_gallery_art_temperature(
    session: Optional[Dict[str, Any]] = None,
    req: Optional[Any] = None,
    profile: Optional[Dict[str, Any]] = None,
    default_temp: float = 0.8
) -> float:
    """
    Cascading temperature resolution for Gallery Art Pass:
    1. session.get("sampling_params", {}).get("temperature") (active user session)
    2. req.sampling_overrides.get("temperature") (request body overrides)
    3. profile.get("sampling", {}).get("temperature") (active mode/assistant profile)
    4. default_temp: 0.8
    """
    session_sampling = session.get("sampling_params") if isinstance(session, dict) and isinstance(session.get("sampling_params"), dict) else {}
    profile_sampling = profile.get("sampling") if isinstance(profile, dict) and isinstance(profile.get("sampling"), dict) else {}
    req_overrides = getattr(req, "sampling_overrides", {}) or {}
    if not isinstance(req_overrides, dict):
        req_overrides = {}

    resolved_temp = (
        session_sampling.get("temperature")
        or req_overrides.get("temperature")
        or profile_sampling.get("temperature")
        or default_temp
    )

    try:
        return float(resolved_temp)
    except (TypeError, ValueError):
        return default_temp
