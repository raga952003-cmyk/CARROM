from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.services.scoring_engine import calculate_points_table
from app.services.qualification import (
    promote_qualifiers,
    league_is_complete,
    knockout_has_started,
)
from app.services.audit_service import record_audit
from app.utils.security import verify_admin
from app.utils.serializers import camelize
from typing import Any, Dict, List
import logging

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/standings", tags=["standings"])


def _participants_for(supabase, tournament_id: str) -> List[Dict[str, Any]]:
    """
    Approved entrants, as the objects the points-table engine expects: a profile
    row for singles, a team row (with both players hydrated) for doubles.
    """
    regs = supabase.table("registrations").select(
        "*, player:profiles(*), team:teams(*)"
    ).eq("tournament_id", tournament_id).eq("status", "approved").execute().data or []

    participants = []
    for reg in regs:
        if reg.get("type") == "singles" and reg.get("player"):
            participants.append(reg["player"])
        elif reg.get("type") == "doubles" and reg.get("team"):
            participants.append(reg["team"])
    return participants


def compute_standings(supabase, tournament_id: str) -> Dict[str, Any]:
    tournament = supabase.table("tournaments").select("*").eq(
        "id", tournament_id
    ).execute().data
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found.")
    tournament = tournament[0]

    matches = supabase.table("matches").select("*").eq(
        "tournament_id", tournament_id
    ).order("match_number").execute().data or []

    participants = _participants_for(supabase, tournament_id)

    # Fall back to the names carried on the matches when a tournament was seeded
    # directly (imported fixtures with no registration rows).
    if not participants and matches:
        seen: Dict[str, Dict[str, Any]] = {}
        for m in matches:
            for id_key, name_key in (("player1_id", "player1_name"), ("player2_id", "player2_name")):
                pid = m.get(id_key)
                if pid and pid not in seen:
                    seen[pid] = {"id": pid, "name": m.get(name_key) or "Unknown"}
        participants = list(seen.values())

    rows = calculate_points_table(matches, participants, tournament.get("rules") or {})

    return {
        "tournamentId": tournament_id,
        "tournamentName": tournament.get("name"),
        "format": tournament.get("format"),
        "rules": tournament.get("rules") or {},
        "participantCount": len(participants),
        "standings": [camelize(r) for r in rows],
    }


@router.get("/{tournament_id}")
async def get_standings(tournament_id: str):
    """
    Points table computed server-side from official, confirmed results (spec 74).

    The frontend renders this; it must not derive standings itself, and it
    cannot write to them.
    """
    supabase = get_admin_db()
    try:
        return compute_standings(supabase, tournament_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Standings computation failed for {tournament_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not compute standings.")


@router.get("/{tournament_id}/qualified")
async def get_qualified(tournament_id: str, count: int = Query(4, ge=1, le=64)):
    """Top N of the league table — the group-stage qualification cut."""
    supabase = get_admin_db()
    try:
        result = compute_standings(supabase, tournament_id)
        top = result["standings"][:count]
        for row in top:
            row["isQualified"] = True
        return {
            "tournamentId": tournament_id,
            "qualifyingCount": count,
            "qualified": top,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Qualification computation failed for {tournament_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not compute qualification.")


@router.post("/{tournament_id}/promote")
async def promote_league_qualifiers(
    tournament_id: str,
    force: bool = Query(False, description="Promote before every league match is confirmed"),
    admin = Depends(verify_admin),
):
    """
    Fill the knockout bracket from the league standings (spec 68, 74).

    Runs automatically when the last league result is confirmed; this endpoint
    exists to re-run it, or to seed the bracket early with force=true.
    """
    admin_db = get_admin_db()
    try:
        matches = admin_db.table("matches").select("*").eq(
            "tournament_id", tournament_id
        ).execute().data or []
        if not matches:
            raise HTTPException(status_code=404, detail="This tournament has no fixtures yet.")
        if not any(m.get("stage") == "knockout" for m in matches):
            raise HTTPException(
                status_code=409,
                detail="This tournament has no knockout stage to promote into.",
            )

        complete, confirmed, total = league_is_complete(matches)
        if not complete and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"The league is not finished ({confirmed}/{total} results confirmed). "
                    "Confirm the remaining matches, or pass force=true to seed the bracket now."
                ),
            )
        if knockout_has_started(matches) and not force:
            raise HTTPException(
                status_code=409,
                detail="The knockout stage has already started; re-seeding would rewrite live matches.",
            )

        standings = compute_standings(admin_db, tournament_id).get("standings", [])
        result = promote_qualifiers(admin_db, tournament_id, standings, matches)

        record_audit(
            admin_db, actor=admin, action="tournament.promote_qualifiers",
            entity_type="tournament", entity_id=tournament_id,
            new_state={"promoted": result["promoted"]},
            request_context={"forced": force, "leagueConfirmed": f"{confirmed}/{total}"},
        )

        return {
            "status": "success",
            "message": f"Promoted {result['promotedCount']} qualifier slot(s) into the knockout bracket.",
            **result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Qualifier promotion failed for {tournament_id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
