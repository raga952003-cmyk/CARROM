"""
Defects found by auditing the whole flow, each pinned so it cannot come back.

Every case here is a bug that was live in this application and is now fixed.
The full suite passed both BEFORE and AFTER each fix, because nothing covered
the behaviour -- which is the reason this file exists. A failure here means a
fix has been undone, not merely refactored.

  1. A walkover left the league incomplete forever, so the knockout could
     never be seeded and the winner scored nothing for the match.
     (qualification.py, scoring_engine.py)
  2. ...and it must still not contribute boards nobody played to the
     tie-break the qualifying cut is read on. (scoring_engine.py)
  3. Removing an entrant rewrote every opponent's standings. The reject
     guard missed walkovers and unplayed fixtures, and DELETE
     /players/{id} checked nothing at all.
     (entry_integrity.py, registrations.py, players.py)
  4. A cancelled or completed tournament stayed fully playable. (matches.py)
  5. A singles-only tournament accepted doubles entries. (tournaments.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness                                     # noqa: E402
from app.services.qualification import league_is_complete       # noqa: E402
from app.services.scoring_engine import calculate_points_table  # noqa: E402
from app.services.scheduling_engine import (                    # noqa: E402
    generate_conflict_free_schedule,
)
from app.routers.standings import compute_standings              # noqa: E402

RESULTS = {}
MATCH_ID = "22222222-2222-2222-2222-222222222222"


def check(label, cond, example=""):
    slot = RESULTS.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not cond:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(str(example)[:300])
    return bool(cond)


def detail(r):
    try:
        return str(r.json().get("detail"))
    except Exception:
        return r.text[:200]


def _tid(t):
    return t["id"] if isinstance(t, dict) else t


def _league(entrants=20, walkover_at=None):
    """A finished round robin; optionally one match awarded, not played."""
    parts = [{"id": "p%d" % i, "name": "Player %d" % i}
             for i in range(1, entrants + 1)]
    matches, n = [], 0
    for i in range(1, entrants + 1):
        for j in range(i + 1, entrants + 1):
            n += 1
            matches.append({
                "id": "m%d" % n, "stage": "league", "match_number": n,
                "player1_id": "p%d" % i, "player2_id": "p%d" % j,
                "player1_board_wins": 2, "player2_board_wins": 1,
                "player1_total_points": 20, "player2_total_points": 10,
                "winner_id": "p%d" % i, "result_confirmed": True,
                "status": "completed",
            })
    if walkover_at is not None:
        matches[walkover_at].update({
            "result_confirmed": False, "walkover": True, "walkover_by": "admin",
            "player1_board_wins": 2, "player2_board_wins": 0,
            "player1_total_points": 0, "player2_total_points": 0,
        })
    return parts, matches


def test_walkover_completes_the_league():
    """One no-show must not leave the league unfinishable."""
    _parts, matches = _league(20, walkover_at=0)
    complete, settled, total = league_is_complete(matches)
    check("a walkover counts as a finished league match",
          complete and settled == total == 190,
          "%s %d/%d" % (complete, settled, total))

    _p2, played = _league(20)
    c2, s2, t2 = league_is_complete(played)
    check("an all-played league still reads complete",
          c2 and s2 == t2 == 190, "%s %d/%d" % (c2, s2, t2))

    # The opposite error would be just as bad: an unplayed fixture must
    # still block completion, or this "fix" would seed a bracket early.
    _p3, pending = _league(20)
    pending[5]["result_confirmed"] = False
    pending[5]["status"] = "scheduled"
    c3, s3, _t3 = league_is_complete(pending)
    check("an unplayed fixture still blocks completion",
          (not c3) and s3 == 189, "%s %d" % (c3, s3))


def test_walkover_awards_the_match_but_not_the_boards():
    parts, matches = _league(20, walkover_at=0)
    rules = {"pointsForWin": 2, "pointsForLoss": 0, "pointsForDraw": 1}
    by = {r["participantName"]: r
          for r in calculate_points_table(matches, parts, rules)}
    winner, loser = by["Player 1"], by["Player 2"]

    check("the walkover winner is awarded the match",
          (winner["played"], winner["won"], winner["points"]) == (19, 19, 38),
          (winner["played"], winner["won"], winner["points"]))
    check("the walkover adds no boards to the winner",
          (winner["boardWins"], winner["boardLosses"]) == (36, 18),
          (winner["boardWins"], winner["boardLosses"]))
    check("the walkover adds no board losses to the loser",
          (loser["boardWins"], loser["boardLosses"]) == (36, 18),
          (loser["boardWins"], loser["boardLosses"]))
    check("the walkover does not move NSD",
          winner["scoreDiff"] == 180, winner["scoreDiff"])

    # Counterfactual: the same match PLAYED must move the digits, or every
    # assertion above could be passing because the match was dropped again.
    played = [dict(m) for m in matches]
    played[0].update({"walkover": False, "walkover_by": None,
                      "result_confirmed": True})
    by2 = {r["participantName"]: r
           for r in calculate_points_table(played, parts, rules)}
    check("the same match PLAYED does move the digits",
          by2["Player 1"]["boardWins"] == 38
          and by2["Player 2"]["boardLosses"] == 20,
          (by2["Player 1"]["boardWins"], by2["Player 2"]["boardLosses"]))


def _delete_case(over):
    h = Harness()
    admin = h.make_user("Org", role="admin")
    anita, bala = h.make_user("Anita"), h.make_user("Bala")
    h.seed_match(_tid(h.seed_tournament(admin)), anita, bala, **over)
    return h.delete("/api/players/%s" % anita, user_id=admin)


def test_deleting_a_player_cannot_rewrite_other_peoples_results():
    cases = [
        ({"status": "live", "result_confirmed": False}, 409,
         "an entrant with a fixture still to play"),
        ({"status": "completed", "result_confirmed": True}, 409,
         "an entrant with a confirmed result"),
        ({"status": "completed", "result_confirmed": False,
          "walkover": True, "walkover_by": "x"}, 409,
         "an entrant with a WALKOVER"),
        ({"status": "cancelled", "result_confirmed": False}, 200,
         "an entrant whose only match was cancelled"),
    ]
    for over, want, what in cases:
        r = _delete_case(over)
        check("DELETE /players and %s" % what, r.status_code == want,
              "%s %s" % (r.status_code, detail(r)))

    h = Harness()
    admin = h.make_user("Org", role="admin")
    solo = h.make_user("Solo")
    r = h.delete("/api/players/%s" % solo, user_id=admin)
    check("DELETE /players still allows a player with no matches",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))


def test_a_closed_tournament_is_not_playable():
    for status, want in (("in_progress", 200), ("cancelled", 409),
                         ("completed", 409)):
        h = Harness()
        admin = h.make_user("Org", role="admin")
        a, b = h.make_user("Anita"), h.make_user("Bala")
        h.seed_match(_tid(h.seed_tournament(admin, status=status)), a, b)

        r = h.post("/api/matches/%s/boards/1/submit" % MATCH_ID,
                   json={"p1Score": 25, "p2Score": 10}, user_id=admin)
        check("submitting a board respects tournament state (%s)" % status,
              r.status_code == want, "%s %s" % (r.status_code, detail(r)))

        r = h.post("/api/matches/%s/walkover" % MATCH_ID,
                   json={"winnerId": a, "reason": "no show"}, user_id=admin)
        check("recording a walkover respects tournament state (%s)" % status,
              r.status_code == want, "%s %s" % (r.status_code, detail(r)))


def test_entry_type_must_match_the_tournament():
    for category in ("singles", "doubles", "both"):
        for entry in ("singles", "doubles"):
            h = Harness()
            admin = h.make_user("Org", role="admin")
            p, q = h.make_user("Pat"), h.make_user("Quinn")
            tid = _tid(h.seed_tournament(admin, category=category,
                                         status="registration_open"))
            payload = {"type": entry, "playerId": p}
            if entry == "doubles":
                payload["partnerId"] = q
            r = h.post("/api/tournaments/%s/registrations" % tid,
                       json=payload, user_id=admin)
            want = 200 if category in ("both", entry) else 409
            check("a %s tournament answers a %s entry correctly"
                  % (category, entry), r.status_code == want,
                  "%s %s" % (r.status_code, detail(r)))


def test_knockout_is_not_scheduled_during_the_league():
    """A seat the league has not filled yet cannot be played during it.

    Every constraint in the scheduler is per-participant or per-feeder, and a
    bracket drawn onto a league has neither: its entrants are rank labels, and
    its feeder is the league table rather than another match. So the
    quarter-final was scheduled for 9:00 AM on an idle board -- before a
    single league match had been played -- and validated as conflict-free,
    because at that point it had no participants to collide with anybody.
    """
    league = [{"id": "L%d" % i, "stage": "league", "roundIndex": 0,
               "player1Id": "p%d" % (i % 4 + 1),
               "player2Id": "p%d" % ((i + 1) % 4 + 1)} for i in range(1, 7)]
    knockout = [{"id": "K1", "stage": "knockout", "roundIndex": 1,
                 "player1Id": None, "player2Id": None}]

    out = {m["id"]: m for m in generate_conflict_free_schedule(
        league + knockout, number_of_boards=3, start_date="2026-10-05",
        match_duration_minutes=30, rest_time_minutes=10)}

    def minute(m):
        hhmm, ampm = m["scheduledTime"].rsplit(" ", 1)
        hh, mm = (int(x) for x in hhmm.split(":"))
        if ampm == "PM" and hh != 12:
            hh += 12
        if ampm == "AM" and hh == 12:
            hh = 0
        return hh * 60 + mm

    last_league = max(minute(out["L%d" % i]) for i in range(1, 7))
    check("a knockout seat is not scheduled before the league ends",
          minute(out["K1"]) >= last_league + 30,
          "knockout at %s, last league at %s"
          % (out["K1"]["scheduledTime"], last_league))

    # A pure knockout has no league to wait for and must not be pushed out.
    only_ko = [{"id": "A", "stage": "knockout", "roundIndex": 0,
                "player1Id": "x", "player2Id": "y"}]
    first = generate_conflict_free_schedule(
        only_ko, number_of_boards=2, start_date="2026-10-05")[0]
    check("a knockout with no league still starts at the beginning",
          first["scheduledTime"].startswith("9:00"), first["scheduledTime"])


def test_the_table_counts_everyone_who_played():
    """An entry list that disagrees with the fixtures must not delete results.

    Seen in production on a 20-entrant round robin with 169 confirmed
    results: the standings returned participantCount 1 and a single row
    reading "played 0, points 0", because one approved registration was all
    that remained. The pool came from registrations still 'approved', and
    calculate_points_table drops any match whose two sides are not both in
    the pool -- so each missing entrant took their opponents' matches with
    them. The fallback existed for exactly this but only fired on an EMPTY
    pool, and one surviving row kept it quiet.
    """
    names = ["Player %d" % i for i in range(1, 21)]
    matches, n = [], 0
    for i in range(20):
        for j in range(i + 1, 20):
            n += 1
            matches.append({
                "id": "m%d" % n, "tournament_id": "T", "stage": "league",
                "type": "singles", "match_number": n,
                "player1_id": "p%d" % i, "player2_id": "p%d" % j,
                "player1_name": names[i], "player2_name": names[j],
                "player1_board_wins": 2, "player2_board_wins": 1,
                "player1_total_points": 20, "player2_total_points": 10,
                "winner_id": "p%d" % i, "result_confirmed": True,
                "status": "completed",
            })

    class Q:
        def __init__(self, rows):
            self.rows, self.f = rows, {}

        def select(self, *a, **k):
            return self

        def eq(self, c, v):
            self.f[c] = v
            return self

        def order(self, *a, **k):
            return self

        def range(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            rows = [r for r in self.rows
                    if all(r.get(k) == v for k, v in self.f.items())]
            return type("R", (), {"data": rows})()

    class DB:
        def __init__(self, regs):
            self.regs = regs

        def table(self, name):
            if name == "registrations":
                return Q(self.regs)
            if name == "matches":
                return Q(matches)
            if name == "tournaments":
                return Q([{"id": "T", "format": "round_robin",
                           "category": "singles",
                           "rules": {"pointsForWin": 2, "pointsForDraw": 1,
                                     "pointsForLoss": 0}}])
            return Q([])

    one_left = [{"id": "r0", "tournament_id": "T", "status": "approved",
                 "type": "singles", "player": {"id": "p0", "name": names[0]}}]
    rows = compute_standings(DB(one_left), "T")["categories"][0]["standings"]
    check("everyone in the fixtures appears in the table", len(rows) == 20,
          len(rows))
    check("their results are counted, not discarded",
          sum(r["played"] for r in rows) == 380,
          sum(r["played"] for r in rows))
    top = rows[0]
    check("the leader's record survives a broken entry list",
          (top["played"], top["points"]) == (19, 38),
          (top["played"], top["points"]))

    # A healthy entry list must behave identically -- the restore is a repair,
    # not a second source of entrants that could double-count anybody.
    whole = [{"id": "r%d" % i, "tournament_id": "T", "status": "approved",
              "type": "singles", "player": {"id": "p%d" % i, "name": names[i]}}
             for i in range(20)]
    healthy = compute_standings(DB(whole), "T")["categories"][0]["standings"]
    check("a complete entry list gives exactly the same table",
          len(healthy) == 20
          and sum(r["played"] for r in healthy) == 380,
          (len(healthy), sum(r["played"] for r in healthy)))


_MID = "22222222-2222-2222-2222-222222222222"
_COINS = {"scoringMode": "remaining_coins", "tieBreak": "most_board_wins",
          "queenPoints": 3, "coinsPerSide": 9, "numberOfSets": 1,
          "setWinnerRule": "total_points", "pointsForWin": 2,
          "pointsForDraw": 1, "pointsForLoss": 0, "maxBoardsPerMatch": 3}


def _scored(stage, spec, tiebreak="most_board_wins"):
    """A match with `spec` boards submitted through the real route."""
    h = Harness()
    admin = h.make_user("Org", role="admin")
    a, b = h.make_user("Anita"), h.make_user("Bala")
    rules = dict(_COINS, tieBreak=tiebreak)
    tid = _tid(h.seed_tournament(admin, status="in_progress", rules=rules))
    h.seed_match(tid, a, b, boards=len(spec), stage=stage, status="live",
                 match_number=1)
    for n, (s1, s2, w) in enumerate(spec, start=1):
        h.post("/api/matches/%s/boards/%d/submit" % (_MID, n),
               json={"p1Score": s1, "p2Score": s2, "boardWinner": w},
               user_id=admin)
    return h, admin, a, b


# Level on POINTS, 2-1 on BOARDS: with tieBreak 'most_board_wins' the engine
# decides this for player 1. The old /confirm threw that verdict away and
# re-derived a one-dimensional points lead, which reads 0.
_BOARD_DECIDED = [(3, 0, "player1"), (3, 0, "player1"), (0, 6, "player2")]


def test_confirm_keeps_the_winner_the_engine_decided():
    """Confirm must not re-decide a match the engine already settled.

    The REOPEN is what makes this bite, and the first version of this test
    left it out and passed against the broken code. submit_board writes the
    engine's winner onto the row, and confirm preserves what is already
    there; /reopen clears winner_id (matches.py:1508), so only the SECOND
    confirm takes the branch that re-derived the result from a
    one-dimensional points lead -- 0 here, because these boards are level on
    points and decided on board wins.
    """
    def confirm_after_reopen(stage):
        h, admin, a, _b = _scored(stage, _BOARD_DECIDED)
        h.post("/api/matches/%s/confirm" % _MID, json={}, user_id=admin)
        h.post("/api/matches/%s/reopen" % _MID,
               json={"reason": "checking the sheet"}, user_id=admin)
        r = h.post("/api/matches/%s/confirm" % _MID, json={}, user_id=admin)
        body = r.json() if r.status_code == 200 else {}
        won = (body or {}).get("winnerId") or (body or {}).get("winner_id")
        row = h.db.table("matches").select("*").eq("id", _MID).execute().data[0]
        return r, won or row.get("winner_id"), row

    r, won, row = confirm_after_reopen("league")
    check("a league match decided on board wins is not re-confirmed as a draw",
          r.status_code == 200 and won, "%s winner=%r drawn_row=%r"
          % (r.status_code, won, row.get("winner_id")))

    r, won, _row = confirm_after_reopen("knockout")
    check("the same knockout match can still be confirmed after a reopen",
          r.status_code == 200 and won,
          "%s %s" % (r.status_code, detail(r)))


def test_a_ruling_cannot_take_a_decided_match_off_its_winner():
    h, admin, _a, b = _scored("league", _BOARD_DECIDED)
    r = h.post("/api/matches/%s/tie-break" % _MID,
               json={"winnerId": b, "reason": "organiser ruling"}, user_id=admin)
    check("a ruling is refused on a match that already has a winner",
          r.status_code == 409, "%s %s" % (r.status_code, detail(r)))

    # Over-correction guard: a GENUINELY level match must still accept one.
    h, admin, a, _b = _scored("league", [(4, 0, "player1"), (0, 4, "player2")],
                              tiebreak="additional_board")
    r = h.post("/api/matches/%s/tie-break" % _MID,
               json={"winnerId": a, "reason": "organiser ruling"}, user_id=admin)
    check("a genuinely level match still accepts a ruling",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))

def test_money_collected_outside_razorpay_can_be_refunded():
    """A UPI receipt is money in hand, so handing it back must be recordable.

    An approved receipt writes its ledger row as 'gpay-proof-<id>' with no
    razorpay_payment_id (026_payment_proof_reconsideration.sql). Both refund
    endpoints excluded exactly that shape -- record_manual_refund wanted
    'manual-', reconcile_refund wanted a razorpay_payment_id -- so a refund
    handed over in real life could be recorded NOWHERE. The entry kept
    payment_status='paid' and status='approved', and both the standings pool
    and fixture generation are built from approved entries.
    """
    def case(order_id):
        h = Harness()
        admin = h.make_user("Org", role="admin")
        pat = h.make_user("Pat")
        tid = _tid(h.seed_tournament(admin, status="registration_open",
                                     entry_fee=300.0, gpay_upi_id="organiser@upi"))
        h.db.seed("registrations", [{
            "id": "reg1", "tournament_id": tid, "player_id": pat,
            "type": "singles", "status": "approved",
            "payment_status": "paid", "fee_paise": 30000}])
        h.db.seed("payments", [{
            "id": "pay1", "tournament_id": tid, "registration_id": "reg1",
            "razorpay_order_id": order_id, "razorpay_payment_id": None,
            "status": "paid", "amount_paise": 30000, "notes": {}}])
        r = h.post("/api/payments/pay1/record-manual-refund",
                   json={"reference": "UTR123456", "reason": "withdrew"},
                   user_id=admin)
        reg = h.db.table("registrations").select("*").eq("id", "reg1").execute().data[0]
        pay = h.db.table("payments").select("*").eq("id", "pay1").execute().data[0]
        return r, pay, reg

    r, pay, reg = case("gpay-proof-abc123")
    check("a UPI receipt refund can be recorded",
          r.status_code == 200 and pay.get("status") == "refunded",
          "%s %s" % (r.status_code, pay.get("status")))
    check("and the refunded entry leaves the approved pool",
          reg.get("status") != "approved" and reg.get("payment_status") != "paid",
          "%s/%s" % (reg.get("status"), reg.get("payment_status")))

    r, pay, _reg = case("manual-desk-1")
    check("a desk payment refund still records",
          r.status_code == 200 and pay.get("status") == "refunded",
          "%s %s" % (r.status_code, pay.get("status")))

    # Over-correction guard: a real Razorpay charge must still go through
    # reconciliation, which verifies against their API.
    r, pay, _reg = case("order_realrazorpay")
    check("a real Razorpay charge is still sent to reconciliation",
          r.status_code == 409 and pay.get("status") == "paid",
          "%s %s" % (r.status_code, detail(r)))

def test_an_unverifiable_token_asks_supabase_instead_of_signing_you_out():
    """"Cannot verify here" must not be answered as "invalid".

    _verify_locally tried to tell the two apart and could not. python-jose
    raises "The specified alg value is not allowed", which contains "alg" but
    NOT "algorithm", so the fallback test matched nothing and the 401 ran
    instead. Supabase projects now default to ECC (ES256) signing keys, so on
    such a project every authenticated request answered "Session expired or
    invalid" straight after a correct sign-in; the client clears its token on
    401, so the next call said "Missing bearer access token" and the user was
    signed out on the spot. A rotated or mistyped JWT secret did the same.

    Only a local SUCCESS may skip the round trip. Everything else defers to
    Supabase, whose answer is final.
    """
    import base64
    import json
    import time
    from fastapi import HTTPException
    from jose import jwt as jose_jwt
    from app.utils import security

    def b64(obj):
        return base64.urlsafe_b64encode(
            json.dumps(obj).encode()).decode().rstrip("=")

    secret = "the-configured-secret"
    claims = {"sub": "abc", "aud": "authenticated",
              "exp": int(time.time()) + 3600}
    expired = dict(claims, exp=int(time.time()) - 10)

    cases = [
        ("a token signed with the configured secret verifies locally",
         jose_jwt.encode(claims, secret, algorithm="HS256"), "local"),
        ("a token signed with a DIFFERENT secret falls back",
         jose_jwt.encode(claims, "some-other-secret", algorithm="HS256"), "fallback"),
        ("an ES256 token falls back rather than signing the user out",
         b64({"alg": "ES256", "typ": "JWT"}) + "." + b64(claims) + ".sig", "fallback"),
        ("an RS256 token falls back rather than signing the user out",
         b64({"alg": "RS256", "typ": "JWT"}) + "." + b64(claims) + ".sig", "fallback"),
        ("an expired token falls back, for Supabase to refuse",
         jose_jwt.encode(expired, secret, algorithm="HS256"), "fallback"),
    ]

    previous = security.settings.SUPABASE_JWT_SECRET
    security.settings.SUPABASE_JWT_SECRET = secret
    try:
        for label, token, want in cases:
            try:
                got = "local" if security._verify_locally(token) else "fallback"
            except HTTPException as exc:
                got = "401:%s" % exc.detail
            check(label, got == want, "got %r, wanted %r" % (got, want))
    finally:
        security.settings.SUPABASE_JWT_SECRET = previous

def main():
    for fn in (test_walkover_completes_the_league,
               test_walkover_awards_the_match_but_not_the_boards,
               test_deleting_a_player_cannot_rewrite_other_peoples_results,
               test_a_closed_tournament_is_not_playable,
               test_entry_type_must_match_the_tournament,
               test_knockout_is_not_scheduled_during_the_league,
               test_the_table_counts_everyone_who_played,
               test_confirm_keeps_the_winner_the_engine_decided,
               test_a_ruling_cannot_take_a_decided_match_off_its_winner,
               test_money_collected_outside_razorpay_can_be_refunded,
               test_an_unverifiable_token_asks_supabase_instead_of_signing_you_out):
        fn()

    total = sum(v[1] for v in RESULTS.values())
    failed = [(k, v) for k, v in sorted(RESULTS.items()) if v[0]]
    print("=" * 78)
    print("regressions (defects found by audit, pinned so they cannot return)")
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
