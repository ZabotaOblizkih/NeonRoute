"""
Character card gallery routes for Roleplay mode.
"""

import asyncio
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter

from config import CHARACTERS_DIR
from services.tavern_parser import TavernParser, CharacterCard

router = APIRouter()


class CharacterSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    avatar_url: str
    archetype: str
    author: Optional[str] = None
    description: Optional[str] = None
    personality: Optional[str] = None
    scenario: Optional[str] = None
    first_mes: Optional[str] = None
    tags: List[str] = []


def _scan_characters_sync() -> List[CharacterSummary]:
    """Scans CHARACTERS_DIR and parses valid character cards."""
    results: List[CharacterSummary] = []
    if not CHARACTERS_DIR.exists():
        return results

    for p in sorted(CHARACTERS_DIR.glob("*.json"), key=lambda f: f.name.lower()):
        try:
            card = TavernParser.parse_file(p)
            if not card or not card.name:
                continue

            cid = p.stem
            archetype = getattr(card, "archetype_label", "Персонаж")

            desc = (card.description_ru or card.description or "").strip()
            personality = (card.personality_ru or card.personality or "").strip() or None
            scenario = (card.scenario_ru or card.scenario or "").strip() or None
            first_mes = (card.first_mes_ru or card.first_mes or "").strip() or None
            tags = [str(t) for t in card.tags] if isinstance(card.tags, (list, tuple)) else []

            results.append(
                CharacterSummary(
                    id=cid,
                    name=card.name,
                    avatar_url=f"/media/character-avatar/{cid}",
                    archetype=archetype,
                    author=getattr(card, "creator_notes", None) or "Авторский канон",
                    description=desc or "Персонаж для ролевого диалога",
                    personality=personality,
                    scenario=scenario,
                    first_mes=first_mes,
                    tags=tags,
                )
            )
        except Exception:
            continue

    return results


@router.get("", response_model=List[CharacterSummary])
async def list_characters() -> List[CharacterSummary]:
    """
    Returns list of character cards for the mobile gallery.
    """
    return await asyncio.to_thread(_scan_characters_sync)
