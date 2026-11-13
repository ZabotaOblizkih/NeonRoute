"""
Media serving routes: avatars and generated images with ETag support.
"""

import hashlib
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from config import (
    CHARACTERS_DIR, OUTPUTS_DIR, ASSETS_DIR,
    DEFAULT_ASSISTANT_AVATAR, DEFAULT_ENGINEER_AVATAR, DEFAULT_ROLEPLAY_AVATAR
)
from services.mode_coordinator import AppMode
from services.session_manager import SessionManager

router = APIRouter()


def _generate_fallback_svg(title: str = "NR", color: str = "#00e5ff", bg: str = "#121422") -> str:
    """Generates an aesthetic SVG avatar fallback."""
    initials = "".join([part[0] for part in title.split() if part])[:2].upper() or "NR"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
  <defs>
    <radialGradient id="avatarGlow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{color}" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="{bg}" stop-opacity="1"/>
    </radialGradient>
  </defs>
  <rect width="100" height="100" rx="50" fill="url(#avatarGlow)" stroke="{color}" stroke-width="3" stroke-opacity="0.6"/>
  <text x="50" y="58" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="34" font-weight="700" fill="{color}" text-anchor="middle" dominant-baseline="middle">{initials}</text>
</svg>"""


def _svg_response(request: Request, title: str, color: str) -> Response:
    svg_data = _generate_fallback_svg(title, color=color)
    etag = f'"{hashlib.sha256(svg_data.encode("utf-8")).hexdigest()[:16]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})
    return Response(
        content=svg_data,
        media_type="image/svg+xml",
        headers={"ETag": etag, "Cache-Control": "public, max-age=3600"},
    )


@router.get("/avatar/{session_id}")
async def get_session_avatar(session_id: str, request: Request):
    """
    Returns the avatar image for a given chat session.
    Safe fallbacks to character avatar, Elaria avatar, or mode-styled SVG.
    """
    char_name = "Элария"
    data = SessionManager.load_session(session_id)

    if isinstance(data, dict):
        mode = AppMode.from_session(data)

        if mode == AppMode.ROLEPLAY:
            char_data = data.get("character_data") or {}
            char_name = char_data.get("name") or "Персонаж"
            cid = data.get("character_id") or char_data.get("id")
            avatar_path = SessionManager.resolve_character_avatar_path(character_id=cid, char_data=char_data)
            if avatar_path:
                return FileResponse(avatar_path)
            if DEFAULT_ROLEPLAY_AVATAR.exists():
                return FileResponse(str(DEFAULT_ROLEPLAY_AVATAR))
            return _svg_response(request, char_name, color="#ff007f")

        if mode == AppMode.ENGINEER:
            asst_av = data.get("assistant_avatar_path")
            if asst_av and Path(asst_av).exists():
                return FileResponse(asst_av)
            for cand in (DEFAULT_ENGINEER_AVATAR, ASSETS_DIR / "elaria_engineer.png", ASSETS_DIR / "elaria_avatar.png", CHARACTERS_DIR / "elaria.png"):
                if cand and cand.exists():
                    return FileResponse(str(cand))
            return _svg_response(request, "ENG", color="#ff9100")

        asst_av = data.get("assistant_avatar_path")
        if asst_av and Path(asst_av).exists():
            return FileResponse(asst_av)
        for cand in (DEFAULT_ASSISTANT_AVATAR, ASSETS_DIR / "elaria_avatar.png", CHARACTERS_DIR / "elaria.png"):
            if cand and cand.exists():
                return FileResponse(str(cand))
        return _svg_response(request, "EL", color="#00e5ff")

    return _svg_response(request, char_name, color="#00e5ff")


@router.get("/character-avatar/{character_id}")
async def get_character_avatar(character_id: str, request: Request):
    """
    Returns the avatar image for a character by card ID or filename stem.
    """
    cid = character_id.strip()
    avatar_path = SessionManager.resolve_character_avatar_path(character_id=cid)
    if avatar_path:
        return FileResponse(avatar_path)
    return _svg_response(request, cid, color="#ff007f")


@router.get("/image")
async def get_image_file(path: str = Query(..., description="Absolute path to generated image")):
    """
    Safely serves generated images within OUTPUTS_DIR or CHARACTERS_DIR.
    """
    try:
        target = Path(path).resolve()
        outputs_res = OUTPUTS_DIR.resolve()
        chars_res = CHARACTERS_DIR.resolve()

        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="Image file not found")

        if not (outputs_res in target.parents or chars_res in target.parents):
            raise HTTPException(status_code=403, detail="Access outside media directory is denied")

        return FileResponse(target)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
