"""
League -> knockout qualification (spec 68, 74).

A league_knockout tournament is generated with its knockout slots empty and
labelled "League Rank #n"; a group_knockout draw labels them "Group A #1" and so
on. Once the league is decided those labels are resolved against the official
standings and the real qualifiers are written into the bracket.

The label carries the seeding the bracket was built with, so promoting by label
preserves it: rank 1 lands in the slot that was drawn for the top seed.

Singles and doubles are separate competitions inside one tournament, each with
its own table and its own bracket, so every slot is resolved against the table
of the category its match belongs to.
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


def slot_is_waiting(match: Dict[str, Any], slot: str) -> bool:
    """Whether a knockout slot still carries a rank label rather than an entrant."""
    label = (match.get(f"{slot}_name") or "").strip()
    return bool(RANK_LABEL.match(label) or GROUP_LABEL.match(label))


def category_of(match: Dict[str, Any]) -> str:
    return match.get("type") or "singles"


def tables_by_category(standings: Any) -> Dict[Optional[str], List[Dict[str, Any]]]:
    """
    The rows to promote from, keyed by category.

    Takes what compute_standings returns (its `categories` are read), that list
    of category blocks on its own, or a bare list of rows -- the shape this took
    before tables were split by category -- which then serves every category.
    """
    if isinstance(standings, dict):
        standings = standings.get("categories") or []
    tables: Dict[Optional[str], List[Dict[str, Any]]] = {}
    for entry in standings or []:
        if isinstance(entry, dict) and "category" in entry and "standings" in entry:
            tables[entry["category"]] = list(entry.get("standings") or [])
        else:
            tables.setdefault(None, []).append(entry)
    return tables


def promote_qualifiers(
    admin_db,
    tournament_id: str,
    standings: Any,
    matches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Fill every labelled slot in the knockout stage from the standings.

    Each match is resolved against the table of its own category. This used to
    take one flat list -- whichever category compute_standings listed first --
    so in a tournament running singles and doubles side by side the doubles
    bracket was filled with singles players, and the doubles table was never
    read at all.

    Returns a summary of what was promoted. Slots whose rank exceeds the number
    of ranked participants are left empty rather than filled with a placeholder.
    """
    tables = tables_by_category(standings)
    promoted: List[Dict[str, Any]] = []
    unresolved: List[str] = []

    for match in matches:
        if match.get("stage") != "knockout":
            continue

        category = category_of(match)
        table = tables.get(category)
        if table is None:
            table = tables.get(None, [])
        by_rank = {row.get("rank"): row for row in table}

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
                rows = [r for r in table if (r.get("group") or r.get("groupName")) == group_name]
                rows.sort(key=lambda r: r.get("rank", 999))
                row = rows[rank - 1] if len(rows) >= rank else None
            else:
                group_name = None
                rank = int(rank_hit.group(1))
                row = by_rank.get(rank)

            if not row:
                unresolved.append(f"{category}: {label}")
                continue

            patch[f"{slot}_id"] = row.get("participantId")
            patch[f"{slot}_name"] = row.get("participantName")
            promoted.append({
                "matchNumber": match.get("match_number"),
                "category": category,
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
    Promote as soon as a category's league stage is fully confirmed.

    Each category is judged on its own: the singles league finishing fills the
    singles bracket even while the doubles league is still being played, and a
    doubles knockout already under way does not stop the singles one from being
    seeded. Judging the whole tournament at once held every bracket back until
    the last category was done.

    Best-effort: called after a result is confirmed, and must never break that
    confirmation, so every failure is logged rather than raised. Returns None
    when no category was ready, otherwise one summary across all of them.
    """
    try:
        matches = admin_db.table("matches").select("*").eq(
            "tournament_id", tournament_id
        ).execute().data or []

        if not any(m.get("stage") == "knockout" for m in matches):
            return None  # not a league_knockout tournament

        standings = None
        summary: Dict[str, Any] = {"promoted": [], "promotedCount": 0, "unresolved": []}
        attempted = False

        for category in sorted({category_of(m) for m in matches}):
            cat_matches = [m for m in matches if category_of(m) == category]
            knockout = [m for m in cat_matches if m.get("stage") == "knockout"]
            if not knockout:
                continue

            complete, confirmed, total = league_is_complete(cat_matches)
            if not complete:
                continue
            if knockout_has_started(cat_matches):
                continue  # already under way; do not rewrite the bracket

            # Nothing to do if no slot is still waiting on a rank label.
            if not any(slot_is_waiting(m, slot) for m in knockout for slot in ("player1", "player2")):
                continue

            if standings is None:
                # Imported here because standings imports this module.
                from app.routers.standings import compute_standings
                standings = compute_standings(admin_db, tournament_id)

            attempted = True
            result = promote_qualifiers(admin_db, tournament_id, standings, cat_matches)
            summary["promoted"].extend(result["promoted"])
            summary["unresolved"].extend(result["unresolved"])
            summary["promotedCount"] += result["promotedCount"]
            logger.info(
                f"{category} league complete for {tournament_id} ({confirmed}/{total}); "
                f"promoted {result['promotedCount']} qualifier slot(s)."
            )

        return summary if attempted else None
    except Exception as e:
        logger.error(f"Auto-promotion failed for tournament {tournament_id}: {str(e)}")
        return None
