"""
Fixtures domain (spec 61, 68).

Fixture generation itself lives in `services/fixture_engine.py` and is applied by
`routers/tournaments.generate_fixtures`. This router exposes it under its own
domain path and adds the read side, which the tournament router never had.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin
from app.services.access_control import require_tournament_access
from app.utils.serializers import serialize_match
from app.utils.idempotency import IdempotencyGuard, get_idempotency_key
from app.routers.tournaments import generate_fixtures as _generate_fixtures
from typing import Optional

router = APIRouter(prefix="/fixtures", tags=["fixtures"])


@router.get("/{tournament_id}")
async def list_fixtures(
    tournament_id: str,
    stage: Optional[str] = Query(None, pattern="^(league|knockout)$"),
    round_index: Optional[int] = Query(None, ge=1),
):
    """Generated fixtures for a tournament, optionally filtered by stage/round."""
    supabase = get_admin_db()
    try:
        query = supabase.table("matches").select("*").eq("tournament_id", tournament_id)
        if stage:
            query = query.eq("stage", stage)
        if round_index is not None:
            query = query.eq("round_index", round_index)

        matches = query.order("match_number").execute().data or []
        match_ids = [m["id"] for m in matches]

        boards_by_match = {}
        if match_ids:
            boards = supabase.table("boards").select("*").in_(
                "match_id", match_ids
            ).order("board_number").execute().data or []
            for b in boards:
                boards_by_match.setdefault(b["match_id"], []).append(b)

        return [serialize_match(m, boards=boards_by_match.get(m["id"], [])) for m in matches]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{tournament_id}/generate")
async def generate(
    tournament_id: str,
    admin = Depends(verify_admin),
    idempotency_key: str = Depends(get_idempotency_key),
):
    """
    Deterministic fixture generation (spec 68). Regenerating replaces the
    previous draw, so this is guarded by an optional Idempotency-Key.
    """
    # Regenerating replaces someone's entire draw, so it belongs to whoever
    # runs that tournament, not to any admin who knows its id.
    require_tournament_access(get_admin_db(), tournament_id, admin)

    guard = IdempotencyGuard(
        get_admin_db(), idempotency_key,
        f"POST /fixtures/{tournament_id}/generate", {"tournament_id": tournament_id},
    )
    cached = guard.replay()
    if cached is not None:
        return cached

    result = await _generate_fixtures(tournament_id, admin)
    guard.store(result)
    return result
