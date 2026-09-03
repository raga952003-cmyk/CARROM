"""
Offline tests for the DB-free logic.

NOTHING HERE TOUCHES THE NETWORK OR A DATABASE. Every other suite in
backend/tests/ drives the live API and writes real rows; this one imports the
pure modules directly and can be run at any time, including during an event.

    python tests/offline/test_pure_logic.py

Coverage is by exhaustive enumeration rather than hand-picked examples: the
board scorer alone is evaluated over every combination of winner, coins
remaining, queen state and penalties, and each result is checked against a set
of invariants that must hold for all of them. Exit code is the number of
distinct invariants violated.
"""
import itertools
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, BACKEND)

from app.services.scoring_engine import (            # noqa: E402
    board_result, queen_award, apply_queen_points, calculate_board_winner,
    recalculate_match_scores, set_layout, scoring_mode,
)
from app.services.fixture_engine import (            # noqa: E402
    generate_round_robin_fixtures, generate_knockout_bracket,
    allocate_groups, suggest_group_count, group_label, create_empty_boards,
)
from app.services.score_validation import validate_board_score  # noqa: E402
from app.services.match_timer import freeze_timer_when_finished  # noqa: E402
from app.services.scheduling_engine import format_time_slot     # noqa: E402

SIDES = ("player1", "player2")

# label -> [failures, total, examples]
RESULTS = {}
KNOWN = {}


def check(label, cond, example=""):
    slot = RESULTS.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not cond:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(example)


def observe(label, cond, example=""):
    """A behaviour worth reporting that is not necessarily a defect."""
    slot = KNOWN.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not cond:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(example)


# ---------------------------------------------------------------------------
# board_result -- the remaining-coins scorer, enumerated exhaustively
# ---------------------------------------------------------------------------

def suite_board_result():
    winners = ("none", "player1", "player2")
    remaining_with = (None, "none", "player1", "player2")
    remaining_counts = (None, 0, 1, 5, 9, 10, 19, 99)
    pocketed_pairs = ((None, None), (0, 0), (3, 6), (9, 0), (9, 9))
    queen_pocketed = ("none", "player1", "player2")
    queen_covered = ("none", "player1", "player2")
    penalties = ((0, 0), (2, 0), (0, 50))
    rules = {"coinsPerSide": 9, "queenPoints": 3, "coinValue": 1}
    coins_per_side = 9
    queen_points = 3

    for w, rw, rc, pocket, qp, qc, pen in itertools.product(
        winners, remaining_with, remaining_counts, pocketed_pairs,
        queen_pocketed, queen_covered, penalties
    ):
        p1p, p2p = pocket
        pen1, pen2 = pen
        out = board_result(
            winner=w, p1_coins_pocketed=p1p, p2_coins_pocketed=p2p,
            coins_remaining_with=rw, coins_remaining=rc,
            queen_pocketed_by=qp, queen_covered_by=qc,
            p1_penalty=pen1, p2_penalty=pen2, rules=rules,
        )
        ctx = ("winner=%s remaining_with=%s remaining=%s pocketed=(%s,%s) "
               "queen=%s/%s pen=(%s,%s) -> %s" %
               (w, rw, rc, p1p, p2p, qp, qc, pen1, pen2,
                dict((k, out[k]) for k in ("player1_score", "player2_score",
                                           "base_points", "queen_bonus",
                                           "queen_awarded_to", "queen_status"))))

        check("scores are never negative",
              out["player1_score"] >= 0 and out["player2_score"] >= 0, ctx)

        check("board_winner is echoed back unchanged",
              out["board_winner"] == (w if w in SIDES else "none"), ctx)

        if w == "none":
            check("no winner means no base points", out["base_points"] == 0, ctx)

        if rw in SIDES and rw == w:
            check("a winner who also holds coins scores no base points",
                  out["base_points"] == 0, ctx)
            check("that contradiction is warned about",
                  any("both the board winner" in x for x in out["warnings"]), ctx)

        check("queen bonus is non-zero only when the queen is covered",
              (out["queen_bonus"] > 0) == (out["queen_status"] == "covered"
                                           and qp in SIDES), ctx)

        check("queen_awarded_to is set exactly when a bonus was paid",
              (out["queen_awarded_to"] in SIDES) == (out["queen_bonus"] > 0), ctx)

        check("queen bonus equals the configured queen value",
              out["queen_bonus"] in (0, queen_points), ctx)

        # A board is 19 coins. The winner can score at most the 9 the loser
        # still had, plus the queen. Anything above that is not a carrom score.
        check("base points cannot exceed the coins one side can hold",
              out["base_points"] <= coins_per_side, ctx)

        check("a board total cannot exceed 9 coins plus the queen",
              max(out["player1_score"], out["player2_score"])
              <= coins_per_side + queen_points, ctx)

        if rw == "none":
            check("'nobody has coins left' scores no base points",
                  out["base_points"] == 0, ctx)


# ---------------------------------------------------------------------------
# The derived score must survive the validator that guards the same column
# ---------------------------------------------------------------------------

def suite_derived_score_is_valid():
    match = {"target_points": 29}
    rules = {"coinsPerSide": 9, "queenPoints": 3}
    for w in ("player1", "player2"):
        other = "player2" if w == "player1" else "player1"
        for rc in (0, 1, 5, 9, 10, 19, 25, 60, 99, 500):
            out = board_result(
                winner=w, coins_remaining_with=other,
                coins_remaining=rc, queen_pocketed_by="player1",
                queen_covered_by="player1", rules=rules,
            )
            ctx = "winner=%s coins_remaining=%s -> %s/%s" % (
                w, rc, out["player1_score"], out["player2_score"])
            ok = True
            try:
                validate_board_score(out["player1_score"], out["player2_score"], match)
            except Exception:
                ok = False
            check("a score the engine derives is accepted by the validator", ok, ctx)


# ---------------------------------------------------------------------------
# queen_award / apply_queen_points
# ---------------------------------------------------------------------------

def suite_queen():
    for claimed in (None, "none", "player1", "player2", "bogus"):
        for covered in (True, False, None):
            for pts in (0, 1, 3, 5, "3", "x", None):
                rules = {"queenPoints": pts}
                b1, b2, note = queen_award(claimed, covered, rules)
                ctx = "claimed=%r covered=%r queenPoints=%r -> (%s,%s)" % (
                    claimed, covered, pts, b1, b2)
                check("queen bonus is never negative", b1 >= 0 and b2 >= 0, ctx)
                check("at most one side is paid for the queen",
                      not (b1 > 0 and b2 > 0), ctx)
                if claimed not in SIDES:
                    check("an unclaimed queen pays nobody", b1 == 0 and b2 == 0, ctx)
                if claimed in SIDES and not covered:
                    check("an uncovered queen pays nobody", b1 == 0 and b2 == 0, ctx)
                if claimed == "player1" and covered:
                    check("the queen is paid to the side that claimed it",
                          b2 == 0, ctx)
                if claimed == "player2" and covered:
                    check("the queen is paid to the side that claimed it",
                          b1 == 0, ctx)

    for s1 in range(0, 30, 3):
        for s2 in range(0, 30, 3):
            for claimed in ("none", "player1", "player2"):
                for covered in (True, False):
                    a, b, _ = apply_queen_points(s1, s2, claimed, covered,
                                                 {"queenPoints": 3})
                    ctx = "%s/%s claimed=%s covered=%s -> %s/%s" % (
                        s1, s2, claimed, covered, a, b)
                    check("applying the queen never lowers a score",
                          a >= s1 and b >= s2, ctx)
                    check("applying the queen adds at most the queen value",
                          (a - s1) + (b - s2) in (0, 3), ctx)


# ---------------------------------------------------------------------------
# validate_board_score
# ---------------------------------------------------------------------------

def suite_validation():
    for target in (10, 21, 25, 29, 60):
        match = {"target_points": target}
        for p1 in range(0, 70, 3):
            for p2 in range(0, 70, 3):
                raised = False
                try:
                    validate_board_score(p1, p2, match)
                except Exception:
                    raised = True
                ctx = "target=%s %s/%s raised=%s" % (target, p1, p2, raised)
                ceiling = max(target, 60)
                if p1 > ceiling or p2 > ceiling:
                    check("a score above the ceiling is rejected", raised, ctx)
                if p1 >= target and p2 >= target:
                    check("both sides reaching the target is rejected", raised, ctx)
                if p1 < target and p2 < target and p1 <= ceiling and p2 <= ceiling:
                    check("an ordinary score is accepted", not raised, ctx)

    match = {"target_points": 29}
    for p in (-1, -5, -100):
        raised = False
        try:
            validate_board_score(p, 0, match)
        except Exception:
            raised = True
        check("a negative score is rejected", raised, "p1=%s" % p)

    for q in ("player1", "player2"):
        raised = False
        try:
            validate_board_score(0, 0, match, q)
        except Exception:
            raised = True
        check("a queen on a scoreless board is rejected by default", raised,
              "queen=%s" % q)
        raised = False
        try:
            validate_board_score(0, 0, match, q, allow_scoreless_queen=True)
        except Exception:
            raised = True
        check("remaining-coins mode allows a scoreless queen", not raised,
              "queen=%s" % q)

    for q in ("PLAYER1", "p1", "both", ""):
        raised = False
        try:
            validate_board_score(5, 3, match, q)
        except Exception:
            raised = True
        check("an unrecognised queen value is rejected", raised, "queen=%r" % q)


# ---------------------------------------------------------------------------
# Round robin
# ---------------------------------------------------------------------------

def people(n, prefix="p"):
    return [{"id": "%s%d" % (prefix, i), "name": "Player %d" % i,
             "rating": 1500 + i} for i in range(1, n + 1)]


def suite_round_robin():
    for n in range(2, 41):
        parts = people(n)
        ms = generate_round_robin_fixtures("t1", parts, max_boards=3)
        ctx = "n=%d matches=%d" % (n, len(ms))

        check("a round robin plays every pair exactly once",
              len(ms) == n * (n - 1) // 2, ctx)

        pairs = set()
        dupes = 0
        selfplay = 0
        byes = 0
        for m in ms:
            a, b = m.get("player1Id"), m.get("player2Id")
            if a == "__BYE__" or b == "__BYE__":
                byes += 1
            if a == b:
                selfplay += 1
            key = tuple(sorted([str(a), str(b)]))
            if key in pairs:
                dupes += 1
            pairs.add(key)

        check("nobody is drawn against themselves", selfplay == 0, ctx)
        check("no pair is drawn twice", dupes == 0, ctx)
        check("the bye placeholder never reaches a real fixture", byes == 0,
              ctx + " byes=%d" % byes)

        seen = set()
        for m in ms:
            seen.add(m.get("player1Id"))
            seen.add(m.get("player2Id"))
        check("every entrant appears in the draw",
              seen == set(p["id"] for p in parts), ctx)


# ---------------------------------------------------------------------------
# Knockout
# ---------------------------------------------------------------------------

def suite_knockout():
    for n in range(2, 65):
        parts = people(n)
        ms = generate_knockout_bracket("t1", parts, max_boards=3)
        ctx = "n=%d matches=%d" % (n, len(ms))

        check("a single-elimination draw has one match fewer than entrants",
              len(ms) == n - 1, ctx)

        nums = [m["matchNumber"] for m in ms]
        check("match numbers are unique and contiguous",
              sorted(nums) == list(range(1, len(ms) + 1)), ctx)

        placed = []
        for m in ms:
            for slot in ("player1Id", "player2Id"):
                if m.get(slot):
                    placed.append(m[slot])
        check("no entrant is placed in the draw twice",
              len(placed) == len(set(placed)),
              ctx + " placed=%d unique=%d" % (len(placed), len(set(placed))))
        check("every entrant is placed somewhere",
              set(placed) == set(p["id"] for p in parts), ctx)

        for m in ms:
            if m.get("player1Id") and m.get("player2Id"):
                check("nobody meets themselves in the draw",
                      m["player1Id"] != m["player2Id"], ctx + " " + m["id"])

        ids = set(m["id"] for m in ms)
        for m in ms:
            if m.get("nextMatchId"):
                check("a match feeds a match that exists",
                      m["nextMatchId"] in ids,
                      ctx + " %s -> %s" % (m["id"], m["nextMatchId"]))
                check("a feeder names the slot it fills",
                      m.get("nextMatchSlot") in SIDES, ctx + " " + m["id"])

        finals = [m for m in ms if not m.get("nextMatchId")]
        check("the draw converges on exactly one final", len(finals) == 1,
              ctx + " finals=%d" % len(finals))

        # Every match must be reachable and every slot fillable: a later-round
        # match with no feeders and no entrants can never be played.
        feeds = {}
        for m in ms:
            if m.get("nextMatchId"):
                feeds.setdefault(m["nextMatchId"], []).append(m["nextMatchSlot"])
        for m in ms:
            filled = sum(1 for s in ("player1Id", "player2Id") if m.get(s))
            incoming = len(set(feeds.get(m["id"], [])))
            check("every match slot is either filled or fed by another match",
                  filled + incoming == 2,
                  ctx + " %s filled=%d fed=%d" % (m["id"], filled, incoming))


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

def suite_groups():
    for n in range(2, 65):
        gc = suggest_group_count(n)
        check("group count is at least one", gc >= 1, "n=%d gc=%d" % (n, gc))
        if n >= 6:
            check("a suggested group never has fewer than three entrants",
                  n / float(gc) >= 3,
                  "n=%d gc=%d avg=%.2f" % (n, gc, n / float(gc)))

        groups = allocate_groups(people(n), gc)
        sizes = [len(g) for g in groups]
        ctx = "n=%d gc=%d sizes=%s" % (n, gc, sizes)
        check("every entrant lands in a group", sum(sizes) == n, ctx)
        if sizes:
            check("group sizes differ by at most one",
                  max(sizes) - min(sizes) <= 1, ctx)
        flat = [p["id"] for g in groups for p in g]
        check("nobody is placed in two groups", len(flat) == len(set(flat)), ctx)

    for i, expected in ((0, "A"), (1, "B"), (25, "Z"), (26, "AA")):
        check("group labels follow the documented sequence",
              group_label(i) == expected,
              "index=%d got=%s want=%s" % (i, group_label(i), expected))


# ---------------------------------------------------------------------------
# Boards / sets layout
# ---------------------------------------------------------------------------

def suite_layout():
    for boards in range(1, 32):
        for sets in range(1, 6):
            bs = create_empty_boards(boards, sets)
            ctx = "boards=%d sets=%d created=%d" % (boards, sets, len(bs))
            check("a match starts with boards-per-set times sets boards",
                  len(bs) == boards * sets, ctx)
            live = [b for b in bs if b["status"] == "in_progress"]
            check("exactly one board starts live", len(live) == 1, ctx)
            check("the live board is the first of the first set",
                  bool(live) and live[0]["setNumber"] == 1
                  and live[0]["boardNumber"] == 1, ctx)
            for s in range(1, sets + 1):
                in_set = [b for b in bs if b["setNumber"] == s]
                check("board numbers restart in every set",
                      sorted(b["boardNumber"] for b in in_set)
                      == list(range(1, boards + 1)), ctx + " set=%d" % s)

    for sets in (None, 0, 1, 3, "3", "x", -2):
        for per in (None, 0, 8, "8", "x", -1):
            m = {"numberOfSets": sets, "maxBoards": per}
            s, p = set_layout(m, {})
            ctx = "sets=%r per=%r -> (%s,%s)" % (sets, per, s, p)
            check("set layout never returns a non-positive count",
                  s >= 1 and p >= 1, ctx)


# ---------------------------------------------------------------------------
# calculate_board_winner / recalculate_match_scores
# ---------------------------------------------------------------------------

def suite_recalculate():
    for p1 in range(0, 12):
        for p2 in range(0, 12):
            b = {"player1Score": p1, "player2Score": p2, "status": "completed"}
            w = calculate_board_winner(b)
            ctx = "%s/%s -> %s" % (p1, p2, w)
            if p1 > p2:
                check("the higher board score wins the board", w == "player1", ctx)
            elif p2 > p1:
                check("the higher board score wins the board", w == "player2", ctx)
            else:
                check("an equal board has no winner", w not in SIDES, ctx)

    for total in range(1, 9):
        for won_by_p1 in range(0, total + 1):
            boards = []
            for i in range(total):
                if i < won_by_p1:
                    p1s, p2s, bw = 10, 4, "player1"
                else:
                    p1s, p2s, bw = 4, 10, "player2"
                boards.append({
                    "boardNumber": i + 1, "setNumber": 1, "status": "completed",
                    "player1Score": p1s, "player2Score": p2s, "boardWinner": bw,
                })
            match = {"id": "m1", "maxBoards": total, "player1Id": "a",
                     "player2Id": "b", "player1Name": "A", "player2Name": "B",
                     "status": "in_progress"}
            out = recalculate_match_scores(match, boards, {})
            ctx = "total=%d p1wins=%d -> %s" % (total, won_by_p1, dict(
                (k, out.get(k)) for k in ("player1BoardWins", "player2BoardWins",
                                          "status", "winnerId")))
            check("board wins are counted correctly",
                  out.get("player1BoardWins") == won_by_p1
                  and out.get("player2BoardWins") == total - won_by_p1, ctx)
            check("board wins never exceed the boards played",
                  (out.get("player1BoardWins") or 0)
                  + (out.get("player2BoardWins") or 0) <= total, ctx)
            check("points are never negative",
                  (out.get("player1TotalPoints") or 0) >= 0
                  and (out.get("player2TotalPoints") or 0) >= 0, ctx)
            if out.get("winnerId"):
                if won_by_p1 * 2 > total:
                    leader = "a"
                elif won_by_p1 * 2 < total:
                    leader = "b"
                else:
                    leader = None
                check("the declared winner is the side with more boards",
                      out["winnerId"] == leader, ctx)


# ---------------------------------------------------------------------------
# scoring_mode
# ---------------------------------------------------------------------------

def suite_mode():
    for raw in (None, {}, {"scoringMode": "classic"},
                {"scoringMode": "remaining_coins"},
                {"scoring_mode": "remaining_coins"}, {"scoringMode": "nonsense"},
                {"scoringMode": ""}, {"scoringMode": None}, {"scoringMode": 7}):
        m = scoring_mode(raw)
        check("scoring mode is always one of the two known models",
              m in ("classic", "remaining_coins"), "%r -> %r" % (raw, m))
        if isinstance(raw, dict) and raw.get("scoringMode") == "remaining_coins":
            check("an explicit remaining-coins setting is honoured",
                  m == "remaining_coins", "%r -> %r" % (raw, m))
        # Either spelling of the key is honoured, so both have to be absent or
        # invalid before classic is the expected answer.
        configured = (raw or {}).get("scoringMode", (raw or {}).get("scoring_mode"))
        if configured not in ("classic", "remaining_coins"):
            check("an unset or invalid mode falls back to classic",
                  m == "classic", "%r -> %r" % (raw, m))


# ---------------------------------------------------------------------------
# Timer
# ---------------------------------------------------------------------------

def suite_timer():
    for running in (True, False, None):
        for elapsed in (None, 0, 30, 3600):
            for started in (None, 0, 1600000000000):
                cur = {"is_timer_running": running,
                       "timer_elapsed_seconds": elapsed,
                       "timer_started_at": started}
                ctx = "running=%r elapsed=%r started=%r" % (running, elapsed, started)

                patch = freeze_timer_when_finished(dict(cur), {})
                check("an unconfirmed result does not stop the clock",
                      "is_timer_running" not in patch, ctx)

                patch = freeze_timer_when_finished(dict(cur),
                                                   {"result_confirmed": True})
                ctx2 = ctx + " -> %r" % (patch,)
                check("confirming a result stops the clock",
                      patch.get("is_timer_running") is False, ctx2)
                if "timer_elapsed_seconds" in patch:
                    check("banked time is never negative",
                          patch["timer_elapsed_seconds"] >= 0, ctx2)
                    check("banked time never goes backwards",
                          patch["timer_elapsed_seconds"] >= (elapsed or 0), ctx2)


# ---------------------------------------------------------------------------
# Time slots
# ---------------------------------------------------------------------------

def suite_time_slots():
    for minutes in range(0, 1500, 7):
        got = format_time_slot("2026-03-01", minutes)
        check("a time slot is produced for any offset", got is not None,
              "minutes=%d -> %r" % (minutes, got))
    for bad in ("", "not-a-date", None, "2026-13-45"):
        ok = True
        try:
            format_time_slot(bad, 30)
        except Exception as e:
            ok = False
            detail = str(e)[:120]
        check("a malformed date does not raise", ok,
              "%r" % (bad,) if ok else "%r -> %s" % (bad, detail))


# ---------------------------------------------------------------------------
# Sheet parsing -- the "11. Thameem" corruption seen in the live database
# ---------------------------------------------------------------------------

def suite_sheet_parser():
    try:
        from app.services.sheet_parser import read_sheet, parse_participants
    except Exception as e:
        observe("sheet_parser could not be imported", False, str(e)[:150])
        return

    csv = (b"S.No,Name,Email,Phone\n"
           b"1,Ragavendra S,a@example.com,9000000001\n"
           b"2,Santhoshraj R,b@example.com,9000000002\n"
           b"3,Thameem,c@example.com,9000000003\n")
    try:
        sheet = read_sheet(csv, "roster.csv")
        rows, errors, meta = parse_participants(sheet)
        names = [r.get("name") for r in rows]
        ctx = "names=%r errors=%r" % (names, errors[:2])
        check("a serial-number column is not glued onto the name",
              all(not n[:2].strip().rstrip(".").isdigit()
                  for n in names if n), ctx)
        check("every data row is parsed", len(rows) == 3, ctx)
    except Exception as e:
        check("a plain CSV roster parses without raising", False,
              traceback.format_exc()[-250:])

    # A sheet whose name cell already carries the ordinal.
    csv2 = (b"Name,Email\n"
            b"1. Ragavendra S,a@example.com\n"
            b"2. Santhoshraj R,b@example.com\n")
    try:
        rows, errors, meta = parse_participants(read_sheet(csv2, "r2.csv"))
        names = [r.get("name") for r in rows]
        check("an ordinal prefix inside the name cell is stripped",
              names == ["Ragavendra S", "Santhoshraj R"], "names=%r" % names)
    except Exception:
        check("a prefixed roster parses without raising", False,
              traceback.format_exc()[-250:])

    # The separators organisers actually use, and the names that must survive.
    for raw, want in ((b"11. Thameem", "Thameem"),
                      (b"2) Asha", "Asha"),
                      (b"7 - Lokesh S", "Lokesh S"),
                      (b"3: Deepan D", "Deepan D"),
                      (b"1.Ragavendra S", "Ragavendra S"),
                      (b"Srinivasan S", "Srinivasan S"),
                      (b"A. R. Rahman", "A. R. Rahman"),
                      (b"2Fast", "2Fast"),
                      (b"1997", "1997")):
        try:
            rows, _, _ = parse_participants(
                read_sheet(b"Name,Email\n" + raw + b",x@y.com\n", "n.csv"))
            got = rows[0].get("name") if rows else None
            check("a roster ordinal is stripped without damaging the name",
                  got == want, "%r -> %r want %r" % (raw.decode(), got, want))
        except Exception:
            check("a roster ordinal is stripped without damaging the name", False,
                  "%r: %s" % (raw.decode(), traceback.format_exc()[-160:]))

    # Two spellings of one person must collapse to one entrant, not two.
    try:
        rows, _, _ = parse_participants(read_sheet(
            b"Name,Email\n11. Thameem,a@x.com\nThameem,a@x.com\n", "dup.csv"))
        names = [r.get("name") for r in rows]
        check("the same person numbered and unnumbered is one entrant",
              len(set(n.lower() for n in names if n)) == 1, "names=%r" % names)
    except Exception:
        check("the same person numbered and unnumbered is one entrant", False,
              traceback.format_exc()[-200:])

    # A file with no name column has nothing to import and SHOULD be refused --
    # but the refusal has to read like a sentence, because routers hand str(e)
    # straight to the browser.
    for content, label, should_raise in (
        (b"", "empty file", True),
        (b"Name\n", "header only", False),
        (b"\xef\xbb\xbfName,Email\nAsha,a@b.com\n", "utf-8 BOM", False),
        (b"Name,Email\n  ,  \n", "blank row", False),
        (b"Name,Email\nA\xc3\xb1il,x@y.com\n", "non-ascii name", False),
        (b"Name,Email\nAsha,a@b.com\nAsha,a@b.com\n", "duplicate rows", False),
    ):
        raised, message = False, ""
        try:
            parse_participants(read_sheet(content, "t.csv"))
        except Exception as e:
            raised, message = True, str(e)
        if should_raise:
            check("a sheet with no name column is refused", raised, label)
            check("that refusal is a readable sentence",
                  raised and " " in message and "Traceback" not in message,
                  "%s: %s" % (label, message[:120]))
        else:
            check("a degenerate but usable sheet is handled without raising",
                  not raised, "%s: %s" % (label, message[:150]))


def suite_drawn_knockout():
    """
    A knockout match that ends level must ask for a decision.

    An even board count makes this ordinary: eight boards split 4-4 reach
    neither side's target of five. It was recorded as completed with no winner
    and no flag, so the bracket stopped at a match that looked finished.
    """
    for boards_count in (2, 4, 6, 8, 10):
        half = boards_count // 2
        boards = []
        for i in range(boards_count):
            p1_wins = i < half
            boards.append({
                "boardNumber": i + 1, "setNumber": 1, "status": "completed",
                "player1Score": 9 if p1_wins else 3,
                "player2Score": 3 if p1_wins else 9,
                "boardWinner": "player1" if p1_wins else "player2",
            })

        for stage in ("knockout", "league"):
            match = {"id": "m", "maxBoards": boards_count, "stage": stage,
                     "player1Id": "a", "player2Id": "b",
                     "player1Name": "A", "player2Name": "B",
                     "status": "in_progress"}
            out = recalculate_match_scores(match, boards, {})
            ctx = "boards=%d stage=%s -> winner=%r tieBreak=%r status=%r" % (
                boards_count, stage, out.get("winnerId"),
                out.get("tieBreakRequired"), out.get("status"))

            check("a level match has no winner", not out.get("winnerId"), ctx)
            if stage == "knockout":
                check("a level knockout match asks for a tie-break",
                      out.get("tieBreakRequired") is True, ctx)
                check("a level knockout match is not filed as finished",
                      out.get("status") != "completed", ctx)
                check("a tie-break names the rule to apply under it",
                      bool(out.get("tieBreakRule")), ctx)
            else:
                check("a drawn league match stays a draw",
                      not out.get("tieBreakRequired"), ctx)
                check("a drawn league match is finished",
                      out.get("status") == "completed", ctx)

        # One more board separates them, and the match resolves.
        boards.append({"boardNumber": boards_count + 1, "setNumber": 1,
                       "status": "completed", "player1Score": 9,
                       "player2Score": 2, "boardWinner": "player1"})
        match = {"id": "m", "maxBoards": boards_count, "stage": "knockout",
                 "player1Id": "a", "player2Id": "b",
                 "player1Name": "A", "player2Name": "B",
                 "status": "in_progress"}
        out = recalculate_match_scores(match, boards, {})
        ctx = "boards=%d+1 -> winner=%r tieBreak=%r" % (
            boards_count, out.get("winnerId"), out.get("tieBreakRequired"))
        check("an extra board settles a tied knockout",
              out.get("winnerId") == "a" and not out.get("tieBreakRequired"), ctx)


SUITES = [
    ("board_result", suite_board_result),
    ("drawn knockout", suite_drawn_knockout),
    ("derived score vs validator", suite_derived_score_is_valid),
    ("queen", suite_queen),
    ("validation", suite_validation),
    ("round robin", suite_round_robin),
    ("knockout", suite_knockout),
    ("groups", suite_groups),
    ("layout", suite_layout),
    ("recalculate", suite_recalculate),
    ("scoring mode", suite_mode),
    ("timer", suite_timer),
    ("time slots", suite_time_slots),
    ("sheet parser", suite_sheet_parser),
]


def main():
    for name, fn in SUITES:
        try:
            fn()
        except Exception:
            check("suite %s ran to completion" % name, False,
                  traceback.format_exc()[-400:])

    total = sum(v[1] for v in RESULTS.values())
    failed = [(k, v) for k, v in sorted(RESULTS.items()) if v[0]]

    print("=" * 78)
    print("offline pure-logic suite")
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

    noted = [(k, v) for k, v in sorted(KNOWN.items()) if v[0]]
    if noted:
        print("OBSERVATIONS (behaviour worth a decision, not asserted as bugs)")
        print("-" * 78)
        for label, slot in noted:
            bad, ran, examples = slot
            print("  %s -> %d of %d" % (label, bad, ran))
            for ex in examples:
                print("     e.g. %s" % ex)
        print()

    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
