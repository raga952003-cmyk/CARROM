"""
Scheduling domain (spec 61, 69).

Conflict detection runs before anything is committed, so a schedule that would
double-book a player or a board is rejected rather than written.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin
from app.services.access_control import require_tournament_access
from app.utils.serializers import serialize_match
from app.routers.tournaments import (
    generate_schedule as _generate_schedule,
    publish_schedule as _publish_schedule,
)
from typing import Any, Dict, List, Optional
from collections import defaultdict

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


def detect_conflicts(matches: List[Dict[str, Any]],
                     team_members: Optional[Dict[str, List[str]]] = None
                     ) -> List[Dict[str, Any]]:
    """
    Two kinds of clash matter (spec 69):
      * one board hosting two matches in the same slot
      * one participant playing two matches in the same slot

    `team_members` maps a team id to its two player ids. Without it a doubles
    side is only compared as a team, so a person entered in both singles and
    doubles could be double-booked without detection.
    """
    team_members = team_members or {}
    conflicts: List[Dict[str, Any]] = []
    by_slot = defaultdict(list)

    for m in matches:
        date, time = m.get("scheduled_date"), m.get("scheduled_time")
        if not date or not time:
            continue
        by_slot[(date, time)].append(m)

    for (date, time), slot_matches in sorted(by_slot.items()):
        boards_seen: Dict[int, int] = {}
        players_seen: Dict[str, int] = {}

        for m in slot_matches:
            board = m.get("board_number")
            if board in boards_seen:
                conflicts.append({
                    "type": "board_double_booked",
                    "date": date, "time": time, "boardNumber": board,
                    "matchNumbers": [boards_seen[board], m.get("match_number")],
                    "detail": f"Board {board} has two matches at {date} {time}.",
                })
            else:
                boards_seen[board] = m.get("match_number")

            sides = [pid for pid in (m.get("player1_id"), m.get("player2_id")) if pid]
            people = [person for side in sides for person in team_members.get(side, [side])]
            for pid in people:
                if pid in players_seen:
                    conflicts.append({
                        "type": "participant_double_booked",
                        "date": date, "time": time, "participantId": pid,
                        "matchNumbers": [players_seen[pid], m.get("match_number")],
                        "detail": f"A participant is scheduled for two matches at {date} {time}.",
                    })
                else:
                    players_seen[pid] = m.get("match_number")

    return conflicts


def _team_members(admin_db, matches: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Team id -> the two player ids on it, for the doubles sides in `matches`."""
    team_ids = {
        m[k] for m in matches for k in ("player1_id", "player2_id")
        if m.get(k) and (m.get("type") == "doubles")
    }
    if not team_ids:
        return {}
    rows = admin_db.table("teams").select("id, player1_id, player2_id").in_(
        "id", list(team_ids)).execute().data or []
    return {
        r["id"]: [pid for pid in (r.get("player1_id"), r.get("player2_id")) if pid]
        for r in rows
    }


@router.get("/{tournament_id}")
async def get_schedule(tournament_id: str):
    """Scheduled matches plus any conflicts detected in the committed schedule."""
    supabase = get_admin_db()
    try:
        matches = supabase.table("matches").select("*").eq(
            "tournament_id", tournament_id
        ).order("scheduled_date").order("scheduled_time").execute().data or []

        return {
            "tournamentId": tournament_id,
            "matches": [serialize_match(m) for m in matches],
            "conflicts": detect_conflicts(matches, _team_members(supabase, matches)),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{tournament_id}/conflicts")
async def get_conflicts(tournament_id: str):
    """Conflict check on its own, for a pre-publish validation step."""
    supabase = get_admin_db()
    try:
        matches = supabase.table("matches").select("*").eq(
            "tournament_id", tournament_id
        ).execute().data or []
        conflicts = detect_conflicts(matches, _team_members(supabase, matches))
        return {
            "tournamentId": tournament_id,
            "conflictFree": len(conflicts) == 0,
            "conflictCount": len(conflicts),
            "conflicts": conflicts,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{tournament_id}/generate")
async def generate(
    tournament_id: str,
    restMinutes: int = Query(10, ge=0, le=240),
    admin = Depends(verify_admin),
):
    require_tournament_access(get_admin_db(), tournament_id, admin)
    return await _generate_schedule(tournament_id, restMinutes=restMinutes, admin=admin)


@router.post("/{tournament_id}/publish")
async def publish(tournament_id: str, admin = Depends(verify_admin)):
    """
    Publishing is refused while the schedule still contains conflicts
    (spec 69: validate before committing).
    """
    admin_db = get_admin_db()
    require_tournament_access(admin_db, tournament_id, admin)

    matches = admin_db.table("matches").select("*").eq(
        "tournament_id", tournament_id
    ).execute().data or []

    conflicts = detect_conflicts(matches, _team_members(admin_db, matches))
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Schedule has {len(conflicts)} unresolved conflict(s). "
                "Regenerate the schedule before publishing."
            ),
        )

    return await _publish_schedule(tournament_id, admin)
