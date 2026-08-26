"""
League -> knockout qualification (spec 68, 74).

A league_knockout tournament is generated with its knockout slots empty and
labelled "League Rank #n". Once the league is decided those labels are resolved
against the official standings and the real qualifiers are written into the
bracket.

The label carries the seeding the bracket was built with, so promoting by label
preserves it: rank 1 lands in the slot that was drawn for the top seed.
"""
from typing import Any, Dict, List, Optional, Tuple
import logging
import re

logger = logging.getLogger("uvicorn.error")

RANK_LABEL = re.compile(r"League Rank #(\d+)")
GROUP_LABEL = re.compile(r"Group ([A-Z]+) #(\d+)")


def league_is_complete(matches: List[Dict[str, Any]]) -> Tuple[bool, int, int]:
    """(complete, confirmed_count, total_count) over the league stage."""
    league = [m for m in matches if m.get("stage") == "league"]
    confirmed = [m for m in league if m.get("result_confirmed")]
    return (bool(league) and len(confirmed) == len(league), len(confirmed), len(league))


def knockout_has_started(matches: List[Dict[str, Any]]) -> bool:
    return any(
        m.get("stage") == "knockout"
        and (m.get("result_confirmed") or m.get("status") in ("live", "paused", "completed"))
        for m in matches
    )


def promote_qualifiers(
    admin_db,
    tournament_id: str,
    standings: List[Dict[str, Any]],
    matches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Fill every "League Rank #n" slot in the knockout stage from the standings.

    Returns a summary of what was promoted. Slots whose rank exceeds the number
    of ranked participants are left empty rather than filled with a placeholder.
    """
    by_rank = {row.get("rank"): row for row in standings}
    promoted: List[Dict[str, Any]] = []
    unresolved: List[str] = []

    for match in matches:
        if match.get("stage") != "knockout":
            continue

        patch: Dict[str, Any] = {}
        for slot in ("player1", "player2"):
            label = (match.get(f"{slot}_name") or "").strip()

            group_hit = GROUP_LABEL.match(label)
            rank_hit = RANK_LABEL.match(label)
            if not group_hit and not rank_hit:
                continue

            if group_hit:
                # "Group B #2" -> second place in group B's table
                group_name, rank = group_hit.group(1), int(group_hit.group(2))
                table = [r for r in standings if (r.get("group") or r.get("groupName")) == group_name]
                table.sort(key=lambda r: r.get("rank", 999))
                row = table[rank - 1] if len(table) >= rank else None
            else:
                group_name = None
                rank = int(rank_hit.group(1))
                row = by_rank.get(rank)

            if not row:
                unresolved.append(label)
                continue

            patch[f"{slot}_id"] = row.get("participantId")
            patch[f"{slot}_name"] = row.get("participantName")
            promoted.append({
                "matchNumber": match.get("match_number"),
                "slot": slot,
                "rank": rank,
                "group": group_name,
                "participantName": row.get("participantName"),
            })

        if patch:
            admin_db.table("matches").update(patch).eq("id", match["id"]).execute()

    return {
        "promoted": promoted,
        "promotedCount": len(promoted),
        "unresolved": unresolved,
    }


def try_auto_promote(admin_db, tournament_id: str) -> Optional[Dict[str, Any]]:
    """
    Promote as soon as the league stage is fully confirmed.

    Best-effort: called after a result is confirmed, and must never break that
    confirmation, so every failure is logged rather than raised.
    """
    try:
        matches = admin_db.table("matches").select("*").eq(
            "tournament_id", tournament_id
        ).execute().data or []

        if not any(m.get("stage") == "knockout" for m in matches):
            return None  # not a league_knockout tournament

        complete, confirmed, total = league_is_complete(matches)
        if not complete:
            return None
        if knockout_has_started(matches):
            return None  # already under way; do not rewrite the bracket

        # Nothing to do if no slot is still waiting on a rank label.
        def waiting(m, slot):
            label = (m.get(f"{slot}_name") or "").strip()
            return bool(RANK_LABEL.match(label) or GROUP_LABEL.match(label))

        if not any(
            waiting(m, slot)
            for m in matches if m.get("stage") == "knockout"
            for slot in ("player1", "player2")
        ):
            return None

        from app.routers.standings import compute_standings
        standings = compute_standings(admin_db, tournament_id).get("standings", [])

        result = promote_qualifiers(admin_db, tournament_id, standings, matches)
        logger.info(
            f"League complete for {tournament_id} ({confirmed}/{total}); "
            f"promoted {result['promotedCount']} qualifier slot(s)."
        )
        return result
    except Exception as e:
        logger.error(f"Auto-promotion failed for tournament {tournament_id}: {str(e)}")
        return None
