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
from typing import Any, Dict, List, Optional
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


def _fallback_participants(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derive entrants from the fixtures when there are no registration rows."""
    seen: Dict[str, Dict[str, Any]] = {}
    for m in matches:
        for id_key, name_key in (("player1_id", "player1_name"), ("player2_id", "player2_name")):
            pid = m.get(id_key)
            if pid and pid not in seen:
                seen[pid] = {"id": pid, "name": m.get(name_key) or "Unknown"}
    return list(seen.values())


def compute_standings(supabase, tournament_id: str) -> Dict[str, Any]:
    """
    Points tables for the tournament, computed per category (spec 74).

    Singles and doubles are separate competitions even within one tournament,
    so they get separate tables. A combined table would rank a person against
    a two-person team.
    """
    tournament = supabase.table("tournaments").select("*").eq(
        "id", tournament_id
    ).execute().data
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found.")
    tournament = tournament[0]
    rules = tournament.get("rules") or {}

    matches = supabase.table("matches").select("*").eq(
        "tournament_id", tournament_id
    ).order("match_number").execute().data or []

    all_participants = _participants_for(supabase, tournament_id)
    singles_pool = [p for p in all_participants if not p.get("player1_id")]
    doubles_pool = [p for p in all_participants if p.get("player1_id")]

    def group_of(match: Dict[str, Any]) -> Optional[str]:
        position = match.get("bracket_position") or {}
        return position.get("group") if isinstance(position, dict) else None

    categories: List[Dict[str, Any]] = []
    for category, pool in (("singles", singles_pool), ("doubles", doubles_pool)):
        cat_matches = [m for m in matches if (m.get("type") or "singles") == category]
        if not cat_matches and not pool:
            continue
        if not pool:
            pool = _fallback_participants(cat_matches)

        labels = sorted({g for g in (group_of(m) for m in cat_matches) if g})

        if labels:
            # A group stage is several separate mini-leagues. One combined table
            # would rank entrants who never played each other.
            group_blocks = []
            flat: List[Dict[str, Any]] = []
            for label in labels:
                group_matches = [m for m in cat_matches if group_of(m) == label]
                ids = {
                    pid for m in group_matches
                    for pid in (m.get("player1_id"), m.get("player2_id")) if pid
                }
                group_pool = [p for p in pool if p["id"] in ids] or _fallback_participants(group_matches)

                rows = [camelize(r) for r in calculate_points_table(group_matches, group_pool, rules)]
                for r in rows:
                    r["group"] = label
                group_blocks.append({
                    "group": label,
                    "participantCount": len(group_pool),
                    "matchCount": len(group_matches),
                    "standings": rows,
                })
                flat.extend(rows)

            categories.append({
                "category": category,
                "participantCount": len(pool),
                "matchCount": len(cat_matches),
                "groups": group_blocks,
                "standings": flat,
            })
        else:
            rows = calculate_points_table(cat_matches, pool, rules)
            categories.append({
                "category": category,
                "participantCount": len(pool),
                "matchCount": len(cat_matches),
                "groups": [],
                "standings": [camelize(r) for r in rows],
            })

    # `standings` stays on the response as the primary table so existing callers
    # keep working; `categories` is the full per-category breakdown.
    primary = categories[0]["standings"] if categories else []

    return {
        "tournamentId": tournament_id,
        "tournamentName": tournament.get("name"),
        "format": tournament.get("format"),
        "rules": rules,
        "participantCount": len(all_participants),
        "categories": categories,
        "standings": primary,
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
        by_category = []
        for block in result.get("categories", []):
            top = block["standings"][:count]
            for row in top:
                row["isQualified"] = True
            by_category.append({"category": block["category"], "qualified": top})

        return {
            "tournamentId": tournament_id,
            "qualifyingCount": count,
            "categories": by_category,
            "qualified": by_category[0]["qualified"] if by_category else [],
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
