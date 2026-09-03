"""
The correction workflow: reopening a confirmed result, then correcting a board.

Every scoring route refuses a confirmed match and points at "the correction
workflow". These cases drive that workflow end to end through the real app,
against the in-memory database: a knockout match is scored, confirmed so its
winner advances, reopened so the winner comes back out of the next round, and
then corrected -- through the same transactional write the original submission
used, under the same idempotency guard, with the same validation.

Technique: WHITE BOX, one case per refusal branch in reopen_match and
update_board, plus the happy path that ties them together.

    python tests/offline/test_reopen.py
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


def body(response):
    try:
        return response.json()
    except Exception:
        return response.text


def detail(response):
    payload = body(response)
    if isinstance(payload, dict):
        return str(payload.get("detail", payload))
    return str(payload)


# Classic scoring decides a match on board wins, so a two-board knockout match
# is complete after two boards and can be levelled by correcting one of them.
CLASSIC = {"scoringMode": "classic", "queenPoints": 3}
SEMI = "22222222-2222-2222-2222-222222222222"
FINAL = "33333333-3333-3333-3333-333333333333"


def classic_board(winner):
    """What an umpire submits for a classic-scored board the named side won."""
    p1, p2 = (10, 3) if winner == "player1" else (3, 10)
    return {"p1Score": p1, "p2Score": p2, "setNumber": 1, "boardWinner": winner,
            "queenClaimedBy": "none", "queenCovered": False}


def classic_fix(board_number, winner):
    """The same board restated as a correction."""
    p1, p2 = (10, 3) if winner == "player1" else (3, 10)
    return {"boardNumber": board_number, "setNumber": 1, "status": "completed",
            "player1Score": p1, "player2Score": p2, "boardWinner": winner,
            "queenClaimedBy": "none", "queenCovered": False}


def match_row(h, match_id):
    return [m for m in h.db.rows("matches") if m["id"] == match_id][0]


def board_row(h, match_id, board_number):
    return [b for b in h.db.rows("boards")
            if b["match_id"] == match_id and b["board_number"] == board_number][0]


def score_history(h, match_id, board_number=None):
    return [s for s in h.db.rows("score_audit_logs")
            if s["match_id"] == match_id
            and (board_number is None or s.get("board_number") == board_number)]


def bracket(h, owner, boards=2, rules=CLASSIC):
    """
    A knockout semi-final feeding player1 of a final whose other side is known.

    Everyone in it is registered, so a reopen has participants to notify and
    not just the organiser.
    """
    tid = h.seed_tournament(owner_id=owner, rules=rules)
    p1, p2, p3 = h.make_user("P One"), h.make_user("P Two"), h.make_user("P Three")
    h.seed_match(tid, None, p3, boards=boards, id=FINAL, match_number=2,
                 player1_name=None, player2_name="P Three", status="scheduled")
    h.seed_match(tid, p1, p2, boards=boards, id=SEMI, match_number=1,
                 player1_name="P One", player2_name="P Two",
                 next_match_id=FINAL, next_match_slot="player1")
    # The harness numbers board ids per match, so two matches collide on them.
    for b in h.db.tables["boards"]:
        b["id"] = "%s-b%d" % (b["match_id"][:4], b["board_number"])
    h.db.seed("registrations", [
        {"id": "reg-%d" % i, "tournament_id": tid, "player_id": pid, "status": "approved"}
        for i, pid in enumerate((p1, p2, p3))
    ])
    return tid, p1, p2, p3


def play(h, admin, match_id, winners):
    """Score one board per entry in `winners`, in board order."""
    for n, winner in enumerate(winners, start=1):
        r = h.post("/api/matches/%s/boards/%d/submit" % (match_id, n),
                   classic_board(winner), user_id=admin)
        check("a board can be scored on the way to a confirmed result",
              r.status_code == 200, "board %d -> %s %s" % (n, r.status_code, detail(r)))


def confirm(h, admin, match_id):
    r = h.post("/api/matches/%s/confirm" % match_id, {}, user_id=admin)
    check("a decided match can be confirmed", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    return r


# ---------------------------------------------------------------------------
# The whole workflow: confirm, reopen, correct, retry the correction
# ---------------------------------------------------------------------------

def test_reopen_then_correct():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    tid, p1, p2, p3 = bracket(h, owner)

    play(h, owner, SEMI, ["player1", "player1"])
    semi = match_row(h, SEMI)
    check("two board wins out of two decide a classic match",
          semi["status"] == "completed" and semi["winner_id"] == p1,
          "%s winner=%s" % (semi["status"], semi.get("winner_id")))

    confirm(h, owner, SEMI)
    final = match_row(h, FINAL)
    check("confirming advances the winner into the final",
          final.get("player1_id") == p1, final)

    r = h.post("/api/matches/%s/reopen" % SEMI,
               {"reason": "Board 2 was entered the wrong way round"}, user_id=owner)
    check("the owner can reopen a confirmed result", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    payload = body(r)
    check("the reopened match comes back in the match shape",
          isinstance(payload, dict) and payload.get("resultConfirmed") is False
          and payload.get("status") == "live" and "boards" in payload,
          str(payload)[:200])

    semi = match_row(h, SEMI)
    check("reopening clears the confirmation",
          semi["result_confirmed"] is False and semi.get("result_confirmed_at") is None,
          {k: semi.get(k) for k in ("result_confirmed", "result_confirmed_at")})
    check("a reopened match is live again with no completion time",
          semi["status"] == "live" and semi.get("match_completed_at") is None,
          {k: semi.get(k) for k in ("status", "match_completed_at")})

    final = match_row(h, FINAL)
    check("reopening pulls the winner back out of the final",
          final.get("player1_id") is None and final.get("player1_name") is None,
          {k: final.get(k) for k in ("player1_id", "player1_name")})
    check("the other finalist is untouched", final.get("player2_id") == p3, final)

    audits = [a for a in h.db.rows("audit_logs") if a.get("action") == "match.reopen"]
    check("the reopen is in the administrative audit trail", len(audits) == 1,
          [a.get("action") for a in h.db.rows("audit_logs")])
    check("the audit record carries the reason",
          bool(audits) and "wrong way round" in str(audits[0].get("request_context")),
          audits[0].get("request_context") if audits else None)

    history = [s for s in score_history(h, SEMI) if "reopen" in str(s.get("reason")).lower()]
    check("the reopen appears in the score history", len(history) == 1,
          [s.get("reason") for s in score_history(h, SEMI)])

    told = [n for n in h.db.rows("notifications")
            if "reopen" in str(n.get("title", "")).lower()]
    check("both players are told the result was reopened",
          any(n.get("profile_id") == p1 for n in told)
          and any(n.get("profile_id") == p2 for n in told),
          [(n.get("profile_id"), n.get("title")) for n in told][:4])
    check("the notice says why",
          all("wrong way round" in str(n.get("message")) for n in told) and told,
          [n.get("message") for n in told][:2])

    # -- and now the correction the reopen was for -------------------------
    headers = dict(h.auth(owner))
    headers["Idempotency-Key"] = "fix-board-2"
    fix = classic_fix(2, "player2")
    before = len(score_history(h, SEMI, 2))

    first = h.client.put("/api/matches/%s/boards/2?reason=Transposed" % SEMI,
                         json=fix, headers=headers)
    check("a board can be corrected once the result is reopened",
          first.status_code == 200, "%s %s" % (first.status_code, detail(first)))

    semi = match_row(h, SEMI)
    check("the correction levels the match on board wins",
          semi["player1_board_wins"] == 1 and semi["player2_board_wins"] == 1,
          {k: semi.get(k) for k in ("player1_board_wins", "player2_board_wins")})
    check("a levelled knockout match is flagged for a tie-break",
          semi.get("tie_break_required") is True and semi.get("tie_break_rule"),
          {k: semi.get(k) for k in ("tie_break_required", "tie_break_rule")})
    check("a match waiting on a tie-break is live with no winner",
          semi["status"] == "live" and semi.get("winner_id") is None
          and semi.get("match_completed_at") is None,
          {k: semi.get(k) for k in ("status", "winner_id", "match_completed_at")})

    b2 = board_row(h, SEMI, 2)
    check("the corrected board stores the corrected score",
          b2["player1_score"] == 3 and b2["player2_score"] == 10
          and b2.get("board_winner") == "player2",
          {k: b2.get(k) for k in ("player1_score", "player2_score", "board_winner")})
    check("the correction writes one line of score history",
          len(score_history(h, SEMI, 2)) == before + 1,
          [s.get("reason") for s in score_history(h, SEMI, 2)])

    second = h.client.put("/api/matches/%s/boards/2?reason=Transposed" % SEMI,
                          json=fix, headers=headers)
    check("a retried correction replays the first answer",
          second.status_code == 200 and body(second) == body(first),
          "first=%s second=%s %s" % (first.status_code, second.status_code,
                                     str(body(second))[:120]))
    check("a retried correction is not applied twice",
          len(score_history(h, SEMI, 2)) == before + 1,
          [s.get("reason") for s in score_history(h, SEMI, 2)])

    r = h.client.put("/api/matches/%s/boards/2?reason=Transposed" % SEMI,
                     json={**fix, "player2Score": 11}, headers=headers)
    check("the same key with a different correction is refused",
          r.status_code == 409, "%s %s" % (r.status_code, detail(r)))


# ---------------------------------------------------------------------------
# Each way a reopen is refused
# ---------------------------------------------------------------------------

def test_reopen_refusals():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    helper = h.make_user("Helper", "admin")
    tid, p1, p2, p3 = bracket(h, owner)

    r = h.post("/api/matches/%s/reopen" % SEMI, {"reason": "Too early"}, user_id=owner)
    check("an unconfirmed match cannot be reopened", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))

    play(h, owner, SEMI, ["player1", "player1"])
    confirm(h, owner, SEMI)

    r = h.post("/api/matches/%s/reopen" % SEMI, {}, user_id=owner)
    check("a reopen with no reason is refused", r.status_code == 422,
          "%s %s" % (r.status_code, detail(r)))
    r = h.post("/api/matches/%s/reopen" % SEMI, {"reason": "   "}, user_id=owner)
    check("a blank reason is a missing reason", r.status_code == 422,
          "%s %s" % (r.status_code, detail(r)))
    check("a refused reopen leaves the result confirmed",
          match_row(h, SEMI)["result_confirmed"] is True, match_row(h, SEMI))

    h.db.seed("tournament_access", [{
        "id": "acc-1", "tournament_id": tid, "user_id": helper,
        "access_role": "scorer", "status": "approved", "decided_by": owner,
    }])
    r = h.post("/api/matches/%s/reopen" % SEMI, {"reason": "Not mine to undo"},
               user_id=helper)
    check("a scorer cannot reopen a result", r.status_code == 403,
          "%s %s" % (r.status_code, detail(r)))
    check("the scorer is told it is the owner's call",
          "owner" in detail(r).lower(), detail(r))

    # The scorer can still score -- here, the first board of the final, which
    # also makes the final a match that is under way.
    r = h.post("/api/matches/%s/boards/1/submit" % FINAL, classic_board("player1"),
               user_id=helper)
    check("an approved scorer can still score a board", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    r = h.post("/api/matches/%s/reopen" % SEMI, {"reason": "Late"}, user_id=owner)
    check("a result whose winner has played on cannot be reopened",
          r.status_code == 409, "%s %s" % (r.status_code, detail(r)))
    check("the refusal names the match that stands in the way",
          "#2" in detail(r) and "reopen" in detail(r).lower(), detail(r))

    r = h.put("/api/matches/%s/boards/2?reason=Oops" % SEMI, classic_fix(2, "player2"),
              user_id=owner)
    check("a confirmed match's boards cannot be corrected", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))
    check("the correction refusal says to reopen first", "Reopen" in detail(r), detail(r))
    b2 = board_row(h, SEMI, 2)
    check("a refused correction changes nothing on the board",
          b2["player1_score"] == 10 and b2["player2_score"] == 3, b2)

    # Take the final back to unplayed and try the other two ways it can block.
    for b in h.db.tables["boards"]:
        if b["match_id"] == FINAL:
            b["status"] = "in_progress" if b["board_number"] == 1 else "pending"
            b["player1_score"] = b["player2_score"] = 0
    final = [m for m in h.db.tables["matches"] if m["id"] == FINAL][0]

    final["status"] = "live"
    r = h.post("/api/matches/%s/reopen" % SEMI, {"reason": "Late"}, user_id=owner)
    check("a live next match blocks the reopen", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))

    final["status"] = "completed"
    final["result_confirmed"] = True
    r = h.post("/api/matches/%s/reopen" % SEMI, {"reason": "Late"}, user_id=owner)
    check("a confirmed next match blocks the reopen", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))

    final["status"] = "scheduled"
    final["result_confirmed"] = False
    r = h.post("/api/matches/%s/reopen" % SEMI, {"reason": "Now it is clear"},
               user_id=owner)
    check("with the next match untouched the reopen goes through",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))
    check("the slot the winner held is empty again",
          match_row(h, FINAL).get("player1_id") is None, match_row(h, FINAL))


# ---------------------------------------------------------------------------
# The correction itself: locked boards, validation, and what it leaves behind
# ---------------------------------------------------------------------------

def test_correction_guards():
    # Remaining-coins: boards lock on submission and the score is derived.
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin)
    p1, p2 = h.make_user("P One"), h.make_user("P Two")
    mid = h.seed_match(tid, p1, p2, boards=2)

    observed = {"p1Score": 0, "p2Score": 0, "setNumber": 1, "boardWinner": "player1",
                "coinsRemainingWith": "player2", "coinsRemaining": 5,
                "queenPocketedBy": "none", "queenCoveredBy": "none"}
    for n in (1, 2):
        r = h.post("/api/matches/%s/boards/%d/submit" % (mid, n), observed, user_id=admin)
        check("a remaining-coins board can be scored", r.status_code == 200,
              "%s %s" % (r.status_code, detail(r)))
    m = match_row(h, mid)
    check("two boards to one side decide a remaining-coins match",
          m["status"] == "completed" and m["winner_id"] == p1
          and m["player1_total_points"] == 10, m)

    fix = {"boardNumber": 2, "setNumber": 1, "status": "completed",
           "boardWinner": "player2", "coinsRemainingWith": "player1", "coinsRemaining": 4}
    r = h.put("/api/matches/%s/boards/2?reason=Fix" % mid, fix, user_id=admin)
    check("a locked board still needs an override to change", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))

    r = h.put("/api/matches/%s/boards/2?reason=Fix&override=true" % mid, fix, user_id=admin)
    check("with an override the locked board is corrected", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    b2 = board_row(h, mid, 2)
    check("the correction is scored from the restated observations",
          b2["player1_score"] == 0 and b2["player2_score"] == 4
          and b2.get("board_winner") == "player2",
          {k: b2.get(k) for k in ("player1_score", "player2_score", "board_winner")})
    m = match_row(h, mid)
    check("the match totals follow the corrected board",
          m["player1_total_points"] == 5 and m["player2_total_points"] == 4
          and m["winner_id"] == p1 and m["status"] == "completed",
          {k: m.get(k) for k in ("player1_total_points", "player2_total_points",
                                  "winner_id", "status")})
    check("the override is written into the score history",
          any("OVERRIDE" in str(s.get("reason")) for s in score_history(h, mid, 2)),
          [s.get("reason") for s in score_history(h, mid, 2)])

    r = h.put("/api/matches/%s/boards/2?reason=Fix&override=true" % mid,
              {**fix, "coinsRemaining": 99}, user_id=admin)
    if r.status_code == 200:
        b2 = board_row(h, mid, 2)
        check("a stored correction never exceeds what a board can be worth",
              max(b2["player1_score"], b2["player2_score"]) <= 12, b2)
    else:
        check("an out-of-range coin count is refused with a readable reason",
              r.status_code == 422 and "{" not in detail(r),
              "%s %s" % (r.status_code, detail(r)))

    r = h.put("/api/matches/%s/boards/2?reason=Level&override=true" % mid,
              {**fix, "coinsRemaining": 5}, user_id=admin)
    check("a correction can level a remaining-coins match", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    m = match_row(h, mid)
    check("a levelled remaining-coins match is flagged for a tie-break and stays live",
          m.get("tie_break_required") is True and m["status"] == "live"
          and m.get("winner_id") is None and m.get("match_completed_at") is None,
          {k: m.get(k) for k in ("tie_break_required", "status", "winner_id",
                                  "match_completed_at")})

    # Classic: the typed numbers are what gets checked.
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin, rules=CLASSIC)
    p1, p2 = h.make_user("P One"), h.make_user("P Two")
    mid = h.seed_match(tid, p1, p2, boards=3)
    play(h, admin, mid, ["player1", "player1"])
    m = match_row(h, mid)
    check("two wins out of three decide a classic match",
          m["status"] == "completed" and m["winner_id"] == p1, m)

    for score, label in ((999, "an impossible score"), (-1, "a negative score")):
        r = h.put("/api/matches/%s/boards/2?reason=Slip" % mid,
                  {**classic_fix(2, "player1"), "player1Score": score}, user_id=admin)
        check("an out-of-range correction is refused with a readable reason",
              r.status_code == 422 and "{" not in detail(r),
              "%s -> %s %s" % (label, r.status_code, detail(r)))
    b2 = board_row(h, mid, 2)
    check("a refused correction leaves the board as it was",
          b2["player1_score"] == 10 and b2["player2_score"] == 3, b2)

    r = h.put("/api/matches/%s/boards/9?reason=Slip" % mid, classic_fix(9, "player1"),
              user_id=admin)
    check("correcting a board that does not exist is a 404", r.status_code == 404,
          "%s %s" % (r.status_code, detail(r)))

    r = h.put("/api/matches/%s/boards/2?reason=Slip" % mid, classic_fix(2, "player2"),
              user_id=admin)
    check("a correction that takes the deciding board away is accepted",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))
    m = match_row(h, mid)
    check("a match that is no longer decided goes back to live with no winner",
          m["status"] == "live" and m.get("winner_id") is None
          and m.get("match_completed_at") is None
          and m["player1_board_wins"] == 1 and m["player2_board_wins"] == 1,
          {k: m.get(k) for k in ("status", "winner_id", "match_completed_at",
                                  "player1_board_wins", "player2_board_wins")})
    check("a match with boards still to play is not a tie-break",
          not m.get("tie_break_required"), m.get("tie_break_required"))

    # A database without the migration-002 function: the correction must still
    # land, through the same sequential fallback the submission uses.
    from fakedb import PostgrestError

    class _MissingFunction:
        def __init__(self, name):
            self.name = name

        def execute(self):
            raise PostgrestError(
                "Could not find the function public.%s" % self.name, "PGRST202")

    real_rpc = h.db.rpc
    h.db.rpc = lambda name, params=None: _MissingFunction(name)
    try:
        r = h.put("/api/matches/%s/boards/1?reason=Recount" % mid, classic_fix(1, "player2"),
                  user_id=admin)
    finally:
        h.db.rpc = real_rpc
    check("a correction still lands when the transactional RPC is missing",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))
    b1 = board_row(h, mid, 1)
    m = match_row(h, mid)
    check("the fallback writes the board, the history and the match together",
          b1["player2_score"] == 10 and m["winner_id"] == p2 and m["status"] == "completed"
          and any(s.get("reason") == "Recount" for s in score_history(h, mid, 1)),
          {"board": (b1["player1_score"], b1["player2_score"]),
           "match": (m.get("winner_id"), m.get("status")),
           "history": [s.get("reason") for s in score_history(h, mid, 1)]})


SUITES = [
    ("reopen then correct", test_reopen_then_correct),
    ("reopen refusals", test_reopen_refusals),
    ("correction guards", test_correction_guards),
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
    print("reopen and correction suite (real app, in-memory database)")
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
