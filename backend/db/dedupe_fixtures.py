"""
Remove duplicate fixtures left by overlapping draw generations.

Generating a draw deletes the existing matches and writes new ones. Two runs
overlapping therefore produced several copies of every match: the second delete
removed only what existed when it started, and both went on inserting. The
symptoms are a fixture list showing each match two or three times, and "Match
not found" when the screen still points at a copy a later run removed.

For each match number this keeps ONE copy — preferring whichever has been
played — and deletes the rest. Boards go with them by cascade.

    python db/dedupe_fixtures.py <tournament name or id>            # show the plan
    python db/dedupe_fixtures.py <tournament name or id> --apply    # carry it out
"""
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from app.database import get_admin_db


def score_of(match, boards_by_match):
    """How much real play a copy holds, so the fullest one is the one kept."""
    boards = boards_by_match.get(match["id"], [])
    played = sum(1 for b in boards if b.get("status") == "completed")
    has_result = bool(match.get("winner_id") or match.get("result_confirmed"))
    points = (match.get("player1_total_points") or 0) + (match.get("player2_total_points") or 0)
    return (has_result, played, points, len(boards))


def main(target: str, apply: bool) -> int:
    adm = get_admin_db()

    rows = adm.table("tournaments").select("id,name").execute().data or []
    hits = [t for t in rows if t["id"] == target or t["name"].lower() == target.lower()]
    if not hits:
        print("No tournament matches {!r}. Known: {}".format(
            target, ", ".join(sorted(t["name"] for t in rows)) or "none"))
        return 1
    t = hits[0]

    matches = adm.table("matches").select("*").eq("tournament_id", t["id"]).execute().data or []
    if not matches:
        print("{} has no matches.".format(t["name"]))
        return 0

    ids = [m["id"] for m in matches]
    boards_by_match = defaultdict(list)
    # Chunked: a long IN list is rejected by the request layer.
    for start in range(0, len(ids), 100):
        for b in (adm.table("boards").select("id,match_id,status")
                  .in_("match_id", ids[start:start + 100]).execute().data or []):
            boards_by_match[b["match_id"]].append(b)

    by_number = defaultdict(list)
    for m in matches:
        by_number[(m.get("match_number"), m.get("stage"), m.get("round_name"))].append(m)

    keep, drop = [], []
    for _, copies in by_number.items():
        copies.sort(key=lambda m: score_of(m, boards_by_match), reverse=True)
        keep.append(copies[0])
        drop.extend(copies[1:])

    print("tournament    : {}".format(t["name"]))
    print("matches now   : {}".format(len(matches)))
    print("distinct      : {}".format(len(by_number)))
    print("to delete     : {}".format(len(drop)))
    played_kept = sum(1 for m in keep
                      if any(b.get("status") == "completed" for b in boards_by_match.get(m["id"], [])))
    played_dropped = sum(1 for m in drop
                         if any(b.get("status") == "completed" for b in boards_by_match.get(m["id"], [])))
    print("played kept   : {}".format(played_kept))
    print("played dropped: {}   <-- must be 0".format(played_dropped))

    if played_dropped:
        print("\nRefusing: a copy with played boards would be deleted. That means two "
              "copies of the same fixture were both scored, and which one counts is "
              "a decision for the organiser, not this script.")
        return 1

    if not drop:
        print("\nNothing to remove.")
        return 0

    if not apply:
        print("\nDry run. Re-run with --apply to delete the {} duplicate(s).".format(len(drop)))
        return 0

    removed = 0
    for m in drop:
        try:
            adm.table("matches").delete().eq("id", m["id"]).execute()
            removed += 1
        except Exception as e:
            print("   could not delete match {}: {}".format(m["id"], str(e)[:100]))

    after = adm.table("matches").select("id").eq("tournament_id", t["id"]).execute().data or []
    print("\ndeleted {} duplicate(s); {} matches remain".format(removed, len(after)))
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(args[0], "--apply" in sys.argv))
