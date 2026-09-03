"""
End-to-end match day: draw -> auto-schedule -> per-board queues -> scoring.

This covers the chain the other suites skip. test_system.py runs a tournament
from creation to a champion but never schedules it, so the step that assigns
each match to a physical table was never exercised anywhere -- which is exactly
the step Scorer mode depends on.

The queue reproduced here is the one frontend/src/components/scorer/BoardMode.tsx
builds:

    matches.filter(m => (m.boardNumber || 1) === boardNumber && !m.resultConfirmed)

so what these assertions describe is what an umpire actually sees after opening
"#/board/<n>" from the Scorer mode dropdown.

Offline: the real FastAPI app over HTTP against the in-memory database.

    python tests/offline/test_e2e_matchday.py
"""
import os
import sys
import traceback
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness                       # noqa: E402

RESULTS = {}


def check(label, cond, example=""):
    slot = RESULTS.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not cond:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(str(example)[:300])
    return bool(cond)


def body(r):
    try:
        return r.json()
    except Exception:
        return r.text


def detail(r):
    p = body(r)
    return str(p.get("detail", p)) if isinstance(p, dict) else str(p)


RULES = {
    "scoringMode": "remaining_coins", "queenPoints": 3, "coinsPerSide": 9,
    "targetScore": 29, "pointsForWin": 2, "pointsForDraw": 1, "pointsForLoss": 0,
    "maxBoardsPerMatch": 3,
}


def tournament_payload(fmt, boards):
    return {
        "name": "Match Day %s" % fmt, "description": "", "category": "singles",
        "format": fmt,
        "registrationStartDate": "2026-01-01", "registrationEndDate": "2026-02-01",
        "tournamentStartDate": "2026-03-01", "tournamentEndDate": "2026-03-02",
        "venue": "Hall A", "city": "Chennai",
        "numberOfBoards": boards, "entryFee": 0,
        "rules": dict(RULES), "status": "draft",
    }


def board_queue(matches, board_number):
    """Exactly what BoardMode.tsx puts in front of the umpire."""
    return [m for m in matches
            if (m.get("board_number") or 1) == board_number
            and not m.get("result_confirmed")
            and m.get("player1_id") and m.get("player2_id")]


def build_tournament(h, admin, fmt, entrants, boards):
    r = h.post("/api/tournaments", tournament_payload(fmt, boards), user_id=admin)
    if not check("a tournament can be created for match day", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return None
    tid = body(r).get("id")

    for i in range(entrants):
        rp = h.post("/api/players", {"name": "Entrant %d" % (i + 1),
                                     "email": "md%d@carrom.example.com" % i,
                                     "rating": 1500 + i}, user_id=admin)
        if rp.status_code != 200:
            check("every entrant can be added", False, detail(rp))
            return None
        h.post("/api/tournaments/%s/registrations" % tid,
               {"type": "singles", "playerId": body(rp).get("id")}, user_id=admin)

    regs = body(h.get("/api/tournaments/%s/registrations" % tid, admin))
    for reg in (regs if isinstance(regs, list) else []):
        if reg.get("status") == "pending":
            h.post("/api/registrations/%s/approve" % reg["id"], {}, user_id=admin)

    rf = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    if not check("the draw can be generated", rf.status_code == 200,
                 "%s %s" % (rf.status_code, detail(rf))):
        return None
    return tid


# ---------------------------------------------------------------------------
# The gap: what Scorer mode shows before and after Auto-Schedule
# ---------------------------------------------------------------------------

def test_boards_are_empty_until_scheduled():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = build_tournament(h, admin, "round_robin", entrants=6, boards=4)
    if not tid:
        return

    matches = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    check("a six-entrant round robin draws fifteen matches",
          len(matches) == 15, "matches=%d" % len(matches))

    # The engine stamps a placeholder boardNumber of 1 on every fixture, but
    # _board_for() in routers/tournaments.py:713 overrides it as the draw is
    # written, dealing fixtures round-robin across the venue's boards. So the
    # umpires have work the moment the draw exists -- scheduling is about WHEN,
    # not about WHERE.
    spread = Counter(m.get("board_number") or 1 for m in matches)
    check("the draw itself spreads fixtures across the venue's boards",
          len(spread) > 1, "distribution=%s" % dict(spread))
    check("the draw never uses a board the venue does not have",
          max(spread) <= 4, "distribution=%s" % dict(spread))
    check("the draw shares the work out evenly",
          max(spread.values()) - min(spread.values()) <= 1,
          "distribution=%s" % dict(spread))

    for n in range(1, 5):
        queue = board_queue(matches, n)
        check("every board has a queue straight after the draw, before scheduling",
              len(queue) > 0, "board %d had %d matches" % (n, len(queue)))

    # ---- Auto-Schedule ------------------------------------------------
    r = h.post("/api/tournaments/%s/schedule?restMinutes=1" % tid, {},
               user_id=admin)
    if not check("Auto-Schedule succeeds", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return

    matches = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    spread = Counter(m.get("board_number") or 1 for m in matches)
    check("Auto-Schedule spreads the matches across the boards",
          len(spread) > 1, "distribution=%s" % dict(spread))
    check("Auto-Schedule never uses a board the tournament does not have",
          max(spread) <= 4, "distribution=%s" % dict(spread))
    check("every match still lands on exactly one board",
          sum(spread.values()) == len(matches),
          "distribution=%s matches=%d" % (dict(spread), len(matches)))

    for n in range(1, 5):
        queue = board_queue(matches, n)
        check("after scheduling, each board has a queue an umpire can work",
              len(queue) > 0, "board %d empty; distribution=%s" % (n, dict(spread)))

    check("every scheduled match is given a time",
          all(m.get("scheduled_time") for m in matches),
          [m.get("scheduled_time") for m in matches[:4]])

    # Nobody can be on two boards at the same moment.
    slots = {}
    clashes = []
    for m in matches:
        key = (m.get("scheduled_date"), m.get("scheduled_time"))
        for pid in (m.get("player1_id"), m.get("player2_id")):
            if pid and (key, pid) in slots:
                clashes.append((key, pid))
            slots[(key, pid)] = m["id"]
    check("no player is scheduled on two boards at the same time",
          not clashes, clashes[:3])

    boards_at_once = Counter()
    for m in matches:
        boards_at_once[(m.get("scheduled_date"), m.get("scheduled_time"))] += 1
    over = [k for k, v in boards_at_once.items() if v > 4]
    check("no time slot uses more boards than the venue has", not over, over[:3])


# ---------------------------------------------------------------------------
# Scoring through the board queues, the way the umpires would
# ---------------------------------------------------------------------------

def test_scoring_from_each_board_queue():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = build_tournament(h, admin, "round_robin", entrants=6, boards=3)
    if not tid:
        return

    r = h.post("/api/tournaments/%s/schedule?restMinutes=1" % tid, {},
               user_id=admin)
    if not check("the schedule is generated before match day", r.status_code == 200,
                 detail(r)):
        return

    score = {"p1Score": 0, "p2Score": 0, "setNumber": 1, "boardWinner": "player1",
             "coinsRemainingWith": "player2", "coinsRemaining": 5,
             "queenPocketedBy": "none", "queenCoveredBy": "none"}

    played = 0
    for board_number in (1, 2, 3):
        matches = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
        for match in board_queue(matches, board_number):
            boards = sorted((b for b in h.db.rows("boards")
                             if b["match_id"] == match["id"]),
                            key=lambda b: b["board_number"])
            for b in boards:
                rs = h.post("/api/matches/%s/boards/%s/submit"
                            % (match["id"], b["board_number"]), score,
                            user_id=admin)
                check("an umpire can score every game on their board",
                      rs.status_code == 200,
                      "board %d match %s game %s -> %s %s"
                      % (board_number, match["id"][:8], b["board_number"],
                         rs.status_code, detail(rs)))
            rc = h.post("/api/matches/%s/confirm" % match["id"], {}, user_id=admin)
            check("an umpire can sign off the match on their board",
                  rc.status_code == 200,
                  "match %s -> %s %s" % (match["id"][:8], rc.status_code,
                                         detail(rc)))
            played += 1

    check("every match in the tournament got played from some board queue",
          played == 15, "played=%d of 15" % played)

    matches = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    check("every match ends confirmed",
          all(m.get("result_confirmed") for m in matches),
          [m["id"][:8] for m in matches if not m.get("result_confirmed")][:3])

    for n in (1, 2, 3):
        check("each board queue is empty once its matches are signed off",
              len(board_queue(matches, n)) == 0,
              "board %d still has %d" % (n, len(board_queue(matches, n))))

    r = h.get("/api/standings/%s" % tid, admin)
    check("the standings can be read once match day is over",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))
    table = body(r)
    rows = table.get("standings") if isinstance(table, dict) else table
    if isinstance(rows, list) and rows:
        played_total = sum(row.get("played", 0) for row in rows)
        check("the table counts every match twice, once per side",
              played_total == 30, "played_total=%d" % played_total)


# ---------------------------------------------------------------------------
# A knockout scheduled across boards must still advance correctly
# ---------------------------------------------------------------------------

def test_knockout_across_boards_still_completes():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = build_tournament(h, admin, "knockout", entrants=8, boards=4)
    if not tid:
        return

    r = h.post("/api/tournaments/%s/schedule?restMinutes=1" % tid, {},
               user_id=admin)
    check("a knockout can be scheduled across boards", r.status_code == 200,
          detail(r))

    score = {"p1Score": 0, "p2Score": 0, "setNumber": 1, "boardWinner": "player1",
             "coinsRemainingWith": "player2", "coinsRemaining": 5,
             "queenPocketedBy": "none", "queenCoveredBy": "none"}

    for _round in range(4):
        for m in h.db.rows("matches"):
            if m["tournament_id"] != tid or m.get("result_confirmed"):
                continue
            if not (m.get("player1_id") and m.get("player2_id")):
                continue
            for b in sorted((x for x in h.db.rows("boards")
                             if x["match_id"] == m["id"]),
                            key=lambda x: x["board_number"]):
                h.post("/api/matches/%s/boards/%s/submit"
                       % (m["id"], b["board_number"]), score, user_id=admin)
            h.post("/api/matches/%s/confirm" % m["id"], {}, user_id=admin)

    matches = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    check("an eight-entrant knockout draws seven matches",
          len(matches) == 7, "matches=%d" % len(matches))
    stuck = [m["id"][:8] for m in matches
             if not (m.get("player1_id") and m.get("player2_id"))]
    check("no knockout match is left waiting for a player that never arrives",
          not stuck, stuck)
    finals = [m for m in matches if not m.get("next_match_id")]
    check("the knockout converges on one final", len(finals) == 1,
          "finals=%d" % len(finals))
    if len(finals) == 1:
        check("scheduling across boards still produces a champion",
              bool(finals[0].get("winner_id")),
              "winner=%r status=%r" % (finals[0].get("winner_id"),
                                       finals[0].get("status")))

    for n in range(1, 5):
        check("every board queue is empty at the end of the event",
              len(board_queue(matches, n)) == 0,
              "board %d has %d" % (n, len(board_queue(matches, n))))


def test_more_boards_than_matches():
    """
    A venue with more boards than the draw needs.

    _board_for deals fixtures round-robin, so the surplus boards get nothing --
    and BoardMode.tsx:102 greets that umpire with a green tick and "Every match
    assigned here is finished", which is not true. Nothing was ever assigned.
    This records the case; the message is what needs fixing, not the dealing.
    """
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = build_tournament(h, admin, "knockout", entrants=2, boards=4)
    if not tid:
        return

    matches = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    check("a two-entrant knockout is a single match", len(matches) == 1,
          "matches=%d" % len(matches))

    used = set(m.get("board_number") or 1 for m in matches)
    check("the one match is dealt to a board the venue has",
          used and max(used) <= 4, used)

    empty = [n for n in range(1, 5) if not board_queue(matches, n)]
    check("a venue with four boards and one match leaves boards idle",
          len(empty) == 3, "idle=%s" % empty)

    # What the umpire on an idle board is told. This is the defect: the screen
    # cannot tell "never assigned" from "all finished", and says the latter.
    check("an idle board is indistinguishable from a finished one",
          len(board_queue(matches, empty[0])) == 0,
          "board %d empty, and BoardMode shows 'Every match assigned here is "
          "finished'" % empty[0])


SUITES = [
    ("boards are populated by the draw", test_boards_are_empty_until_scheduled),
    ("more boards than matches", test_more_boards_than_matches),
    ("scoring from each board queue", test_scoring_from_each_board_queue),
    ("knockout across boards", test_knockout_across_boards_still_completes),
]


def main():
    for name, fn in SUITES:
        try:
            fn()
        except Exception:
            check("the %s suite runs to completion" % name, False,
                  traceback.format_exc()[-400:])

    total = sum(v[1] for v in RESULTS.values())
    failed = [(k, v) for k, v in sorted(RESULTS.items()) if v[0]]
    print("=" * 78)
    print("end-to-end match day (draw -> schedule -> board queues -> scoring)")
    print("=" * 78)
    print("assertions executed : %d" % total)
    print("invariants checked  : %d" % len(RESULTS))
    print("invariants violated : %d" % len(failed))
    print()
    if failed:
        print("FAILURES")
        print("-" * 78)
        for label, slot in failed:
            bad, ran, examples = slot
            print("  %s" % label)
            print("     %d of %d cases failed" % (bad, ran))
            for ex in examples:
                print("     e.g. %s" % ex)
            print()
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
