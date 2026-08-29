"""
Server-side Gemini calls (spec 58, 86).

The two AI features previously ran in the browser with the key supplied as
VITE_GEMINI_API_KEY, which Vite inlines into the production bundle — anyone
loading the deployed site could read it and spend against the account. The key
now lives only on the server as GEMINI_API_KEY and the browser calls these
endpoints instead.

Both endpoints are admin-only and degrade to "unavailable" rather than failing
the page: the callers already have local fallbacks.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.config import settings
from app.utils.security import verify_admin
from typing import Any, Dict, List, Optional
import json
import logging
import httpx

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/ai", tags=["ai"])

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)


class ParseRequest(BaseModel):
    text: str


class PosterRequest(BaseModel):
    tournamentName: Optional[str] = ""
    venue: Optional[str] = ""
    city: Optional[str] = ""
    category: Optional[str] = ""
    format: Optional[str] = ""


def _configured() -> bool:
    return bool(getattr(settings, "GEMINI_API_KEY", ""))


async def _generate(prompt: str, schema_hint: str) -> Dict[str, Any]:
    """
    Ask Gemini for JSON and parse it.

    Returns {"available": False} when no key is configured, so the caller can
    fall back rather than surfacing an error.
    """
    if not _configured():
        return {"available": False}

    payload = {
        "contents": [{"parts": [{"text": f"{prompt}\n\n{schema_hint}\nReturn JSON only."}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7},
    }

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                GEMINI_URL,
                params={"key": settings.GEMINI_API_KEY},
                json=payload,
            )
        if response.status_code != 200:
            logger.warning(f"Gemini returned {response.status_code}: {response.text[:200]}")
            return {"available": False, "error": "The AI service refused the request."}

        body = response.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        return {"available": True, "data": json.loads(text)}
    except json.JSONDecodeError:
        return {"available": False, "error": "The AI service returned something unreadable."}
    except Exception as e:
        logger.warning(f"Gemini call failed: {str(e)}")
        return {"available": False, "error": "Could not reach the AI service."}


@router.get("/status")
async def ai_status(admin = Depends(verify_admin)):
    """Whether the AI features are usable, so the UI can hide them if not."""
    return {"available": _configured()}


@router.post("/parse-participants")
async def parse_participants(data: ParseRequest, admin = Depends(verify_admin)):
    """Turn a pasted list of players into structured entries."""
    if not data.text.strip():
        raise HTTPException(status_code=422, detail="No text to parse.")

    prompt = (
        "You are a data extraction assistant. Parse this raw list of carrom "
        "players into structured entries.\n"
        "1. Extract name, club (default 'Independent'), city (leave blank if absent), "
        "rating (default 1500) and seed (integer or null).\n"
        "2. Strip numbering prefixes like '1.' or '2)', stray symbols and header rows.\n"
        "3. If only a name is present, use the defaults for the rest.\n\n"
        f"Player text:\n---\n{data.text[:8000]}\n---"
    )
    schema = ('Shape: {"players":[{"name":string,"club":string,"city":string,'
              '"rating":number,"seed":number|null}]}')

    result = await _generate(prompt, schema)
    if not result.get("available"):
        return {"available": False, "players": [], "error": result.get("error")}

    players = (result.get("data") or {}).get("players") or []
    return {"available": True, "players": players}


@router.post("/poster-copy")
async def poster_copy(data: PosterRequest, admin = Depends(verify_admin)):
    """Marketing copy for the tournament poster."""
    prompt = (
        "You are a sports branding director specialising in carrom championships. "
        "Write promotional copy for this tournament.\n"
        f"Name: {data.tournamentName}\nVenue: {data.venue}\nCity: {data.city}\n"
        f"Category: {data.category}\nFormat: {data.format}\n"
        "Keep it dignified and federation-appropriate, not hyperbolic."
    )
    schema = ('Shape: {"tagline":string,"highlights":[string,string,string],'
              '"announcement":string,"badgeText":string}')

    result = await _generate(prompt, schema)
    if not result.get("available"):
        return {"available": False, "error": result.get("error")}

    payload = result.get("data") or {}
    return {
        "available": True,
        "tagline": payload.get("tagline", ""),
        "highlights": payload.get("highlights", [])[:3],
        "announcement": payload.get("announcement", ""),
        "badgeText": payload.get("badgeText", ""),
    }
