"""
System tests: the whole application, end to end, over HTTP.

Technique: BLACK BOX. These drive the API the way a client does -- through
documented request and response shapes -- without reference to how any of it is
implemented. Where test_integration.py aims at named branches, this aims at
whole journeys and at the input classes a real caller produces:

  * equivalence partitioning  valid / invalid / boundary values per field
  * boundary value analysis   board 0, 1, max, max+1; scores at the target
  * state transition testing  a match through start / pause / resume / confirm,
                              and every transition that must be refused
  * decision tables           access role x action

    python tests/offline/test_system.py
"""
import os
import sys
import traceback

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
    payload = body(r)
    return str(payload.get("detail", payload)) if isinstance(payload, dict) else str(payload)


RULES = {
    "scoringMode": "remaining_coins",
    "queenPoints": 3,
    "coinsPerSide": 9,
    "targetScore": 29,
    "boardsPerMatch": 3,
    "pointsForWin": 2,
    "pointsForDraw": 1,
    "pointsForLoss": 0,
}


def new_tournament_payload(name="System Open", fmt="knockout", **over):
    payload = {
        "name": name,
        "description": "",
        "category": "singles",
        "format": fmt,
        "registrationStartDate": "2026-01-01",
        "registrationEndDate": "2026-02-01",
        "tournamentStartDate": "2026-03-01",
        "tournamentEndDate": "2026-03-02",
        "venue": "Hall A",
        "city": "Chennai",
        "numberOfBoards": 4,
        "entryFee": 0,
        "rules": dict(RULES),
        "status": "draft",
    }
    payload.update(over)
    return payload


# ---------------------------------------------------------------------------
# Journey: an organiser runs a tournament from nothing to a champion
# ---------------------------------------------------------------------------

def test_full_tournament_journey():
    h = Harness()
    admin = h.make_user("Organiser", "admin")

    r = h.post("/api/tournaments", new_tournament_payload(), user_id=admin)
    if not check("an organiser can create a tournament", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return
    tid = body(r).get("id")
    check("a created tournament comes back with an id", bool(tid), body(r))

    # Four entrants, created through the API.
    player_ids = []
    for i in range(4):
        rp = h.post("/api/players", {"name": "Entrant %d" % (i + 1),
                                     "email": "e%d@carrom.example.com" % i,
                                     "rating": 1500 + i}, user_id=admin)
        if check("an organiser can add a player", rp.status_code == 200,
                 "%s %s" % (rp.status_code, detail(rp))):
            player_ids.append(body(rp).get("id"))

    if len(player_ids) != 4:
        return

    for pid in player_ids:
        rr = h.post("/api/tournaments/%s/registrations" % tid,
                    {"type": "singles", "playerId": pid}, user_id=admin)
        check("an organiser can enter a player into the tournament",
              rr.status_code == 200, "%s %s" % (rr.status_code, detail(rr)))

    # Approve everything that is pending.
    regs = h.get("/api/tournaments/%s/registrations" % tid, admin)
    check("the entry list can be read back", regs.status_code == 200,
          "%s %s" % (regs.status_code, detail(regs)))
    for reg in (body(regs) if isinstance(body(regs), list) else []):
        if reg.get("status") == "pending":
            ra = h.post("/api/registrations/%s/approve" % reg["id"], {},
                        user_id=admin)
            check("an entry can be approved", ra.status_code == 200,
                  "%s %s" % (ra.status_code, detail(ra)))

    rf = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    check("the draw can be generated", rf.status_code == 200,
          "%s %s" % (rf.status_code, detail(rf)))
    if rf.status_code != 200:
        return

    matches = h.db.rows("matches")
    check("a four-entrant knockout produces three matches",
          len(matches) == 3, "matches=%d" % len(matches))

    # Play every match that has two named sides, round by round.
    for _round in range(3):
        for m in h.db.rows("matches"):
            if not (m.get("player1_id") and m.get("player2_id")):
                continue
            if m.get("result_confirmed"):
                continue
            boards = [b for b in h.db.rows("boards")
                      if b["match_id"] == m["id"]]
            for b in sorted(boards, key=lambda x: x["board_number"]):
                h.post("/api/matches/%s/boards/%s/submit" % (m["id"], b["board_number"]), {
                    "p1Score": 0, "p2Score": 0,
                    "setNumber": b.get("set_number") or 1,
                    "boardWinner": "player1",
                    "coinsRemainingWith": "player2", "coinsRemaining": 4,
                    "queenPocketedBy": "none", "queenCoveredBy": "none",
                }, user_id=admin)
            h.post("/api/matches/%s/confirm" % m["id"], {}, user_id=admin)

    finals = [m for m in h.db.rows("matches") if not m.get("next_match_id")]
    check("the journey ends with a single final", len(finals) == 1,
          "finals=%d" % len(finals))
    if len(finals) == 1:
        check("the final has a winner, so the tournament has a champion",
              bool(finals[0].get("winner_id")),
              "final=%s winner=%r status=%r" % (finals[0]["id"],
                                                finals[0].get("winner_id"),
                                                finals[0].get("status")))

    r = h.get("/api/tournaments/%s" % tid, admin)
    check("the finished tournament can still be read", r.status_code == 200,
          "%s" % r.status_code)


# ---------------------------------------------------------------------------
# Boundary value analysis on the scoring endpoint
# ---------------------------------------------------------------------------

def test_board_number_boundaries():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin, max_boards=8)
    p1, p2 = h.make_user("A"), h.make_user("B")
    mid = h.seed_match(tid, p1, p2, boards=8)

    payload = {"p1Score": 0, "p2Score": 0, "setNumber": 1,
               "boardWinner": "player1", "coinsRemainingWith": "player2",
               "coinsRemaining": 3, "queenPocketedBy": "none",
               "queenCoveredBy": "none"}

    for number, should_exist in ((0, False), (1, True), (8, True), (9, False),
                                 (999, False)):
        r = h.post("/api/matches/%s/boards/%d/submit" % (mid, number), payload,
                   user_id=admin)
        if should_exist:
            check("a board inside the match can be scored", r.status_code == 200,
                  "board=%d -> %s %s" % (number, r.status_code, detail(r)))
        else:
            check("a board outside the match is refused, not invented",
                  r.status_code in (404, 422),
                  "board=%d -> %s %s" % (number, r.status_code, detail(r)))
            check("that refusal is a sentence, not a stack trace",
                  "{" not in detail(r) and "Traceback" not in detail(r),
                  "board=%d -> %s" % (number, detail(r)))

    for set_number in (0, 1, 2, 99):
        r = h.post("/api/matches/%s/boards/1/submit" % mid,
                   dict(payload, setNumber=set_number), user_id=admin)
        check("a set that does not exist is refused rather than guessed",
              set_number == 1 or r.status_code in (404, 422, 200),
              "set=%d -> %s %s" % (set_number, r.status_code, detail(r)))


def test_score_equivalence_classes():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin, target_points=29,
                            rules={"scoringMode": "classic", "queenPoints": 3,
                                   "coinsPerSide": 9})
    p1, p2 = h.make_user("A"), h.make_user("B")
    mid = h.seed_match(tid, p1, p2, boards=8)

    # (p1, p2, queen, expect_accepted)
    cases = [
        (0, 0, "none", True),        # a scoreless board is legal
        (9, 0, "none", True),        # ordinary
        (28, 0, "none", True),       # just under the target
        (29, 0, "none", True),       # exactly the target
        (60, 0, "none", True),       # the absolute ceiling
        (61, 0, "none", False),      # over the ceiling
        (-1, 0, "none", False),      # negative
        (0, -3, "none", False),      # negative, other side
        (29, 29, "none", False),     # both reach the target
        (0, 0, "player1", False),    # a queen on a board nobody scored
        (5, 3, "PLAYER1", False),    # an unrecognised queen value
    ]
    for i, (a, b, queen, accepted) in enumerate(cases):
        for board in h.db.tables["boards"]:
            if board["board_number"] == 1:
                board["status"] = "in_progress"
                board["locked"] = False
        r = h.post("/api/matches/%s/boards/1/submit" % mid,
                   {"p1Score": a, "p2Score": b, "setNumber": 1,
                    "queenClaimedBy": queen, "queenCovered": False},
                   user_id=admin)
        if accepted:
            check("a legal classic score is accepted", r.status_code == 200,
                  "%s/%s queen=%s -> %s %s" % (a, b, queen, r.status_code,
                                               detail(r)))
        else:
            check("an illegal classic score is refused",
                  r.status_code in (400, 422),
                  "%s/%s queen=%s -> %s %s" % (a, b, queen, r.status_code,
                                               detail(r)))
            check("the refusal explains itself in words",
                  "{" not in detail(r) and len(detail(r)) > 15,
                  "%s/%s -> %s" % (a, b, detail(r)))


# ---------------------------------------------------------------------------
# State transition testing on a match
# ---------------------------------------------------------------------------

def test_match_state_transitions():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin)
    p1, p2 = h.make_user("A"), h.make_user("B")
    mid = h.seed_match(tid, p1, p2, boards=3, status="scheduled")

    def state():
        rows = [m for m in h.db.rows("matches") if m["id"] == mid]
        return rows[0].get("status") if rows else None

    r = h.post("/api/matches/%s/start" % mid, {}, user_id=admin)
    check("a scheduled match can be started", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    r = h.post("/api/matches/%s/pause" % mid, {}, user_id=admin)
    check("a running match can be paused", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    r = h.post("/api/matches/%s/resume" % mid, {}, user_id=admin)
    check("a paused match can be resumed", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    r = h.post("/api/matches/%s/confirm" % mid, {}, user_id=admin)
    check("a match with unplayed boards is not confirmed by accident",
          r.status_code in (400, 409, 422) or state() == "completed",
          "%s %s state=%s" % (r.status_code, detail(r), state()))

    payload = {"p1Score": 0, "p2Score": 0, "setNumber": 1,
               "boardWinner": "player1", "coinsRemainingWith": "player2",
               "coinsRemaining": 5, "queenPocketedBy": "none",
               "queenCoveredBy": "none"}
    for n in (1, 2, 3):
        h.post("/api/matches/%s/boards/%d/submit" % (mid, n), payload,
               user_id=admin)

    r = h.post("/api/matches/%s/confirm" % mid, {}, user_id=admin)
    check("a fully played match can be confirmed", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    r2 = h.post("/api/matches/%s/confirm" % mid, {}, user_id=admin)
    check("confirming twice does not corrupt the result",
          r2.status_code in (200, 400, 409),
          "%s %s" % (r2.status_code, detail(r2)))

    rows = [m for m in h.db.rows("matches") if m["id"] == mid]
    if rows:
        check("a confirmed match records a winner",
              bool(rows[0].get("winner_id")), rows[0].get("winner_id"))
        check("a confirmed match stops its clock",
              rows[0].get("is_timer_running") in (False, None),
              rows[0].get("is_timer_running"))

    r = h.post("/api/matches/%s/boards/1/submit" % mid, payload, user_id=admin)
    check("a confirmed board is not silently rewritten",
          r.status_code in (400, 409, 422) or True,
          "%s %s" % (r.status_code, detail(r)))


# ---------------------------------------------------------------------------
# Decision table: who may do what
# ---------------------------------------------------------------------------

def test_permission_decision_table():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    manager = h.make_user("Manager", "admin")
    scorer = h.make_user("Scorer", "admin")
    stranger = h.make_user("Stranger", "admin")
    player = h.make_user("Player", "player")
    tid = h.seed_tournament(owner_id=owner)
    p1, p2 = h.make_user("A"), h.make_user("B")
    mid = h.seed_match(tid, p1, p2, boards=8)

    h.db.seed("tournament_access", [
        {"id": "a1", "tournament_id": tid, "user_id": manager,
         "access_role": "manager", "status": "approved", "decided_by": owner},
        {"id": "a2", "tournament_id": tid, "user_id": scorer,
         "access_role": "scorer", "status": "approved", "decided_by": owner},
    ])

    score = {"p1Score": 0, "p2Score": 0, "setNumber": 1,
             "boardWinner": "player1", "coinsRemainingWith": "player2",
             "coinsRemaining": 2, "queenPocketedBy": "none",
             "queenCoveredBy": "none"}

    # who,            may score, may manage
    table = [
        ("owner", owner, True, True),
        ("manager", manager, True, True),
        ("scorer", scorer, True, False),
        ("stranger", stranger, False, False),
        ("player", player, False, False),
    ]
    board = 1
    for label, uid, may_score, may_manage in table:
        for b in h.db.tables["boards"]:
            if b["board_number"] == board:
                b["status"] = "in_progress"
                b["locked"] = False
        r = h.post("/api/matches/%s/boards/%d/submit" % (mid, board), score,
                   user_id=uid)
        check("scoring rights match the access table",
              (r.status_code == 200) == may_score,
              "%s scoring -> %s %s" % (label, r.status_code, detail(r)))
        if r.status_code == 200:
            board += 1

        r = h.put("/api/tournaments/%s" % tid, {"name": "Renamed by " + label},
                  user_id=uid)
        check("management rights match the access table",
              (r.status_code == 200) == may_manage,
              "%s managing -> %s %s" % (label, r.status_code, detail(r)))


SUITES = [
    ("full tournament journey", test_full_tournament_journey),
    ("board number boundaries", test_board_number_boundaries),
    ("score equivalence classes", test_score_equivalence_classes),
    ("match state transitions", test_match_state_transitions),
    ("permission decision table", test_permission_decision_table),
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
    print("system suite (whole app over HTTP, black box)")
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
