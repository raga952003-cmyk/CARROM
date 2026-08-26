"""
Teams domain.

Doubles pairs live in their own table but had no API, so the admin UI's
"Select Doubles Team" dropdown had nothing to render and doubles entry was a
dead end. This exposes the read side plus explicit team creation.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_admin_db
from app.models.player import TeamSchema
from app.utils.security import verify_admin, get_user_profile
from app.utils.serializers import serialize_team
from app.services.audit_service import record_audit
from typing import Any, Dict, List, Optional

router = APIRouter(prefix="/teams", tags=["teams"])


def _hydrate(admin_db, teams: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach the two player profiles each team refers to."""
    if not teams:
        return []

    player_ids = {
        pid
        for t in teams
        for pid in (t.get("player1_id"), t.get("player2_id"))
        if pid
    }
    profiles = {}
    if player_ids:
        rows = admin_db.table("profiles").select("*").in_(
            "id", list(player_ids)
        ).execute().data or []
        profiles = {p["id"]: p for p in rows}

    hydrated = []
    for t in teams:
        t = dict(t)
        t["player1"] = profiles.get(t.get("player1_id"))
        t["player2"] = profiles.get(t.get("player2_id"))
        hydrated.append(serialize_team(t))
    return hydrated


@router.get("")
async def list_teams(tournament_id: Optional[str] = Query(None, alias="tournamentId")):
    """
    All doubles teams, or only those entered in one tournament.

    Teams missing either player profile are omitted: the UI renders
    `player1.name` directly, so a half-resolved team would crash it.
    """
    admin_db = get_admin_db()
    try:
        if tournament_id:
            regs = admin_db.table("registrations").select("team_id").eq(
                "tournament_id", tournament_id
            ).eq("type", "doubles").execute().data or []
            team_ids = [r["team_id"] for r in regs if r.get("team_id")]
            if not team_ids:
                return []
            teams = admin_db.table("teams").select("*").in_(
                "id", team_ids
            ).order("name").execute().data or []
        else:
            teams = admin_db.table("teams").select("*").order("name").execute().data or []

        return [t for t in _hydrate(admin_db, teams) if t.get("player1") and t.get("player2")]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_team(data: TeamSchema, admin = Depends(verify_admin)):
    """Pair two existing players into a team, reusing the pair if it exists."""
    admin_db = get_admin_db()
    try:
        if data.player1_id == data.player2_id:
            raise HTTPException(
                status_code=422,
                detail="A team needs two different players.",
            )

        found = admin_db.table("profiles").select("id, name, club, city").in_(
            "id", [data.player1_id, data.player2_id]
        ).execute().data or []
        if len(found) < 2:
            raise HTTPException(status_code=404, detail="One or both player profiles were not found.")

        by_id = {p["id"]: p for p in found}

        # The unique constraint is on the ordered pair, so check both orders.
        existing = admin_db.table("teams").select("*").or_(
            f"and(player1_id.eq.{data.player1_id},player2_id.eq.{data.player2_id}),"
            f"and(player1_id.eq.{data.player2_id},player2_id.eq.{data.player1_id})"
        ).execute().data or []
        if existing:
            return _hydrate(admin_db, existing)[0]

        p1, p2 = by_id[data.player1_id], by_id[data.player2_id]
        payload = {
            "name": data.name or f"{p1['name']} & {p2['name']}",
            "player1_id": data.player1_id,
            "player2_id": data.player2_id,
            "club": data.club or p1.get("club"),
            "city": data.city or p1.get("city"),
            "rating": data.rating,
            "seed": data.seed,
        }
        created = admin_db.table("teams").insert(payload).execute().data[0]

        record_audit(
            admin_db, actor=admin, action="team.create",
            entity_type="team", entity_id=created["id"], new_state=created,
        )
        return _hydrate(admin_db, [created])[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
