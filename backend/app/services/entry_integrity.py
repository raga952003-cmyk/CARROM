"""
Whether an entrant can be taken out of a tournament without rewriting other
people's records.

The points table builds its pool from registrations that are still 'approved'
(standings._participants_for), and calculate_points_table drops any match whose
two sides are not both in that pool (scoring_engine.py). Matches carry no
foreign key to registrations, so nothing at the database level objects. The
result is that removing ONE entrant silently rewrites every opponent they ever
played -- measured at 20 entrants: deleting one rewrote all 19 other rows, and
deleting the 4th-placed entrant moved the 9th-placed entrant into the top 8.

This lived as a private helper in routers/registrations.py that only looked at
`result_confirmed = True`, which left three ways through:

  - a WALKOVER is a result, but record_walkover does not set result_confirmed,
    so an entrant could be removed along with the matches they were awarded;
  - a fixture still TO BE PLAYED was invisible, so rejecting an entrant left
    their fixtures live -- they then got played, confirmed, and counted for
    nobody;
  - DELETE /api/players/{id} had no equivalent check at all, and it cascades
    (profiles.id -> auth.users ON DELETE CASCADE, registrations.player_id ->
    profiles ON DELETE CASCADE), so it is the same destruction by another
    button in the same admin screen.

One helper, used by both routes, so they cannot drift apart again.
"""
from typing import Any, Dict, List, Optional

from app.services.qualification import is_walkover


def _settled(match: Dict[str, Any]) -> bool:
    """A result is on the books, whether it was played or awarded."""
    return bool(match.get("result_confirmed") or is_walkover(match))


def entanglement(admin_db, tournament_id: str,
                 participant_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """This participant's settled results and still-unplayed fixtures."""
    if not tournament_id or not participant_id:
        return {"settled": [], "outstanding": []}

    rows = admin_db.table("matches").select(
        "match_number, player1_id, player2_id, result_confirmed, status, "
        "walkover, walkover_by"
    ).eq("tournament_id", tournament_id).execute().data or []

    theirs = [m for m in rows
              if participant_id in (m.get("player1_id"), m.get("player2_id"))]
    return {
        "settled": [m for m in theirs if _settled(m)],
        "outstanding": [m for m in theirs
                        if not _settled(m) and m.get("status") != "cancelled"],
    }


def anywhere(admin_db, participant_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """The same question across every tournament, for DELETE of a whole player.

    Asked of the matches table directly rather than through registrations.
    Going via registrations looked natural -- a draw is generated from the
    entry list, so a player with fixtures "must" have a registration row --
    but it is the wrong direction: matches store the participant id, carry no
    foreign key to registrations, and outlive them. A guard that reads the
    entry list cannot see a match the entry list has already lost, which is
    precisely the state this is here to refuse to create.
    """
    if not participant_id:
        return {"settled": [], "outstanding": []}

    cols = ("match_number, player1_id, player2_id, result_confirmed, status, "
            "walkover, walkover_by")
    rows = []
    seen = set()
    # Two equality reads rather than one .or_() -- PostgREST's or= filter takes
    # its own dialect, and this runs against the offline fake as well.
    for side in ("player1_id", "player2_id"):
        found = admin_db.table("matches").select(cols).eq(
            side, participant_id).execute().data or []
        for m in found:
            key = (m.get("match_number"), m.get("player1_id"), m.get("player2_id"))
            if key not in seen:
                seen.add(key)
                rows.append(m)

    return {
        "settled": [m for m in rows if _settled(m)],
        "outstanding": [m for m in rows
                        if not _settled(m) and m.get("status") != "cancelled"],
    }


def refusal_detail(found: Dict[str, List[Dict[str, Any]]],
                   action: str) -> Optional[str]:
    """The message to refuse `action` with, or None if it is safe to proceed."""
    settled, outstanding = found["settled"], found["outstanding"]
    if not settled and not outstanding:
        return None

    def numbers(matches):
        shown = ", ".join("#%s" % m.get("match_number") for m in matches[:5])
        return shown + (", ..." if len(matches) > 5 else "")

    parts = []
    if settled:
        parts.append(
            f"{len(settled)} result(s) already recorded ({numbers(settled)}) -- "
            f"{action} would take those matches out of the points table and "
            "change their opponents' standings too"
        )
    if outstanding:
        parts.append(
            f"{len(outstanding)} fixture(s) still to play ({numbers(outstanding)}) "
            f"-- {action} would leave those on the schedule to be played and "
            "then counted for nobody"
        )
    return (
        "This entrant has " + "; and ".join(parts) + ". Reopen and void the "
        "results, and remove the remaining fixtures, if the entry really must go."
    )
