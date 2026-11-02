"""
Media serving routes: avatars and generated images.
"""

import json
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from config import CHARACTERS_DIR, HISTORY_DIR, OUTPUTS_DIR, ASSETS_DIR

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


@router.get("/avatar/{session_id}")
async def get_session_avatar(session_id: str):
    """
    Returns the avatar image for a given chat session.
    Safe fallbacks to character avatar, Elaria avatar, or mode-styled SVG.
    """
    mode = "assistant"
    char_name = "Элария"

    chat_file = HISTORY_DIR / f"chat_{session_id}.json"
    if chat_file.exists():
        try:
            with open(chat_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            mode = str(data.get("mode") or "assistant").lower()
            if data.get("type") == "engineering":
                mode = "engineer"

            # 1. Roleplay mode character avatar
            if mode == "roleplay":
                char_data = data.get("character_data") or {}
                char_name = char_data.get("name") or "Персонаж"
                av_path_str = char_data.get("avatar_path")
                if av_path_str and Path(av_path_str).exists():
                    return FileResponse(av_path_str)

                # Search characters dir by name/id
                cid = data.get("character_id") or char_data.get("id")
                candidates = []
                if cid:
                    candidates.extend([
                        CHARACTERS_DIR / f"{cid}_avatar.png",
                        CHARACTERS_DIR / f"{cid}.png",
                    ])
                if char_name:
                    safe = "".join(c for c in char_name if c.isalnum() or c in (' ', '_', '-')).strip()
                    candidates.extend([
                        CHARACTERS_DIR / f"{char_name}_avatar.png",
                        CHARACTERS_DIR / f"{char_name}.png",
                        CHARACTERS_DIR / f"{safe}_avatar.png",
                        CHARACTERS_DIR / f"{safe}.png",
                    ])
                for cand in candidates:
                    if cand.exists():
                        return FileResponse(cand)

                # SVG fallback for Roleplay
                svg_data = _generate_fallback_svg(char_name, color="#ff007f")
                return Response(content=svg_data, media_type="image/svg+xml")

            # 2. Engineer mode avatar
            elif mode == "engineer":
                char_name = "Инженер"
                asst_av = data.get("assistant_avatar_path")
                if asst_av and Path(asst_av).exists():
                    return FileResponse(asst_av)
                for cand in (ASSETS_DIR / "elaria_engineer.png", ASSETS_DIR / "elaria_avatar.png", CHARACTERS_DIR / "elaria.png"):
                    if cand.exists():
                        return FileResponse(cand)
                svg_data = _generate_fallback_svg("ENG", color="#ff9100")
                return Response(content=svg_data, media_type="image/svg+xml")

            # 3. Assistant mode avatar
            else:
                char_name = "Элария"
                asst_av = data.get("assistant_avatar_path")
                if asst_av and Path(asst_av).exists():
                    return FileResponse(asst_av)
                for cand in (ASSETS_DIR / "elaria_avatar.png", CHARACTERS_DIR / "elaria.png"):
                    if cand.exists():
                        return FileResponse(cand)
                svg_data = _generate_fallback_svg("EL", color="#00e5ff")
                return Response(content=svg_data, media_type="image/svg+xml")

        except Exception:
            pass

    # Generic mode fallback
    svg_data = _generate_fallback_svg(char_name, color="#00e5ff")
    return Response(content=svg_data, media_type="image/svg+xml")


@router.get("/character-avatar/{character_id}")
async def get_character_avatar(character_id: str):
    """
    Returns the avatar image for a character by card ID or filename stem.
    """
    cid = character_id.strip()
    candidates = [
        CHARACTERS_DIR / f"{cid}_avatar.png",
        CHARACTERS_DIR / f"{cid}.png",
    ]

    # Safe scan without glob pattern issues
    if CHARACTERS_DIR.exists():
        try:
            for p in CHARACTERS_DIR.iterdir():
                if p.is_file() and p.suffix.lower() == ".png":
                    if cid.lower() in p.stem.lower():
                        candidates.append(p)
        except Exception:
            pass

    for cand in candidates:
        if cand.exists() and cand.is_file():
            return FileResponse(cand)

    svg_data = _generate_fallback_svg(cid, color="#ff007f")
    return Response(content=svg_data, media_type="image/svg+xml")


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

        # Guardrail against path traversal
        if not (outputs_res in target.parents or chars_res in target.parents):
            raise HTTPException(status_code=403, detail="Access outside media directory is denied")

        return FileResponse(target)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
