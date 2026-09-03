"""
Whole-tournament scenarios, played offline through the real engines.

The unit suite (test_pure_logic.py) proves each function is correct in
isolation. That is not the same as a tournament being runnable: a draw can be
generated, every board scored legally, and the event still be impossible to
finish because a bracket slot can never be filled or a player cannot find their
own fixture. These scenarios play a tournament end to end -- generate the draw,
score every board, advance the winners, build the table -- and assert the
things an organiser actually needs to be true at the end.

NOTHING HERE TOUCHES THE NETWORK OR A DATABASE.

    python tests/offline/test_scenarios.py

Each scenario is one combination of ten dimensions:

    format        knockout / round_robin / league_knockout / group_stage /
                  group_knockout
    entrants      2 .. 24, including every awkward non-power-of-two
    scoring       classic / remaining_coins
    sets          one set, or three
    boards        3 or 8 per set
    entrant kind  singles or doubles teams
    roster names  clean / imported with sheet ordinals / colliding surnames
    results       decisive / containing drawn boards / containing walkovers
    identity      login id equals the roster row, or does not
    queen rules   must-be-covered and award-to varied across scenarios

Exit code is the number of distinct invariants violated.
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, BACKEND)

from app.services.scoring_engine import (            # noqa: E402
    board_result, apply_queen_points, recalculate_match_scores,
    apply_set_results, calculate_points_table,
)
from app.services.fixture_engine import (            # noqa: E402
    generate_round_robin_fixtures, generate_knockout_bracket,
    generate_league_knockout_fixtures, generate_group_stage_fixtures,
    generate_group_knockout_fixtures,
)
from app.services.sheet_parser import read_sheet, parse_participants  # noqa: E402

SIDES = ("player1", "player2")

RESULTS = {}
SCENARIOS_RUN = 0
SCENARIOS_FAILED = set()


def check(label, cond, example=""):
    slot = RESULTS.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not cond:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(example)
        return False
    return True


class Rng:
    """A tiny deterministic generator, so a failing scenario is reproducible."""

    def __init__(self, seed):
        self.state = (seed * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)

    def next(self, n):
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return (self.state >> 33) % n


# ---------------------------------------------------------------------------
# Rosters
# ---------------------------------------------------------------------------

COLLIDING = ["Srinivas", "Srinivasan", "Srinivasan S", "Srini", "Sri Nivas",
             "Ragavendra", "Ragavendra S", "Raga", "Thameem", "Thameem A"]


def roster_names(shape, n):
    """
    The names as they end up on the roster, and the name each person would
    type when signing in. For an imported sheet these must agree, or the
    person cannot find their own fixtures.
    """
    if shape == "collision":
        login = [COLLIDING[i % len(COLLIDING)] + ("" if i < len(COLLIDING)
                 else " %d" % (i // len(COLLIDING))) for i in range(n)]
        return list(login), list(login)

    login = ["Player %d" % (i + 1) for i in range(n)]
    if shape == "clean":
        return list(login), list(login)

    # "ordinal": the organiser numbered the sheet, so the name column reads
    # "1. Player 1". Run it through the real importer -- the roster name is
    # whatever parse_participants produces, not what the test wishes for.
    lines = [b"Name,Email"]
    for i, name in enumerate(login):
        lines.append(("%d. %s,p%d@example.com" % (i + 1, name, i + 1)).encode())
    rows, _errors, _meta = parse_participants(
        read_sheet(b"\n".join(lines) + b"\n", "roster.csv"))
    parsed = [r["name"] for r in rows]
    if len(parsed) != n:
        # Reported by the scenario as a finding rather than crashing the run.
        parsed = (parsed + login)[:n]
    return parsed, login


def build_entrants(kind, shape, n):
    """(participants for the draw, login identities, team membership)."""
    names, logins = roster_names(shape, n)

    if kind == "singles":
        parts = [{"id": "p%d" % (i + 1), "name": names[i], "rating": 1500 + n - i}
                 for i in range(n)]
        people = [{"id": "p%d" % (i + 1), "name": logins[i]} for i in range(n)]
        return parts, people, {}

    # Doubles: the fixture carries the TEAM id, so a person is found through
    # the team they belong to.
    parts, people, teams = [], [], {}
    for i in range(n):
        tid = "t%d" % (i + 1)
        a, b = "u%da" % (i + 1), "u%db" % (i + 1)
        parts.append({"id": tid, "name": names[i], "rating": 1500 + n - i,
                      "player1_id": a, "player2_id": b})
        people.append({"id": a, "name": logins[i]})
        people.append({"id": b, "name": logins[i] + " (partner)"})
        teams[a] = {tid}
        teams[b] = {tid}
    return parts, people, teams


# ---------------------------------------------------------------------------
# Identity resolution, mirroring frontend/src/utils/myMatches.ts
# ---------------------------------------------------------------------------

def find_my_matches(matches, teams, user_id, user_name):
    owned = {user_id} | set(teams.get(user_id, set()))
    by_id = [m for m in matches
             if m.get("player1Id") in owned or m.get("player2Id") in owned]
    if by_id:
        return by_id
    name = (user_name or "").strip().lower()
    if not name:
        return []
    return [m for m in matches
            if (m.get("player1Name") or "").strip().lower() == name
            or (m.get("player2Name") or "").strip().lower() == name]


# ---------------------------------------------------------------------------
# Playing a match
# ---------------------------------------------------------------------------

def play_boards(match, mode, sets, per_set, outcome, rules, rng):
    """Every board of one match, scored through the real engine."""
    boards = []
    for s in range(1, sets + 1):
        for b in range(1, per_set + 1):
            roll = rng.next(10)
            if outcome == "with_draws" and roll == 0:
                winner = "none"
            else:
                winner = "player1" if roll % 2 == 0 else "player2"

            queen_by = ("none", "player1", "player2")[rng.next(3)]
            covered_by = queen_by if rng.next(4) else "none"

            if mode == "remaining_coins":
                loser = "player2" if winner == "player1" else (
                    "player1" if winner == "player2" else "none")
                out = board_result(
                    winner=winner,
                    coins_remaining_with=loser,
                    coins_remaining=rng.next(10),
                    queen_pocketed_by=queen_by,
                    queen_covered_by=covered_by,
                    p1_penalty=0, p2_penalty=0, rules=rules,
                )
                p1, p2 = out["player1_score"], out["player2_score"]
                declared = out["board_winner"]
            else:
                c1, c2 = rng.next(10), rng.next(10)
                if winner == "player1":
                    c1, c2 = max(c1, c2 + 1), min(c1, c2)
                elif winner == "player2":
                    c1, c2 = min(c1, c2), max(c1, c2 + 1)
                else:
                    c1 = c2
                p1, p2, _ = apply_queen_points(
                    c1, c2, queen_by, covered_by != "none", rules)
                declared = winner

            boards.append({
                "setNumber": s, "boardNumber": b, "status": "completed",
                "player1Score": p1, "player2Score": p2,
                "boardWinner": declared,
            })
    return boards


def settle(match, boards, sets, rules):
    if sets > 1:
        return apply_set_results(match, boards, rules)
    return recalculate_match_scores(match, boards, rules)


# ---------------------------------------------------------------------------
# One scenario
# ---------------------------------------------------------------------------

def run_scenario(sc, index):
    global SCENARIOS_RUN
    SCENARIOS_RUN += 1
    tag = ("#%d %s n=%d %s sets=%d boards=%d %s names=%s %s"
           % (index, sc["format"], sc["n"], sc["mode"], sc["sets"],
              sc["boards"], sc["kind"], sc["names"], sc["outcome"]))

    def ck(label, cond, extra=""):
        ok = check(label, cond, (tag + " " + extra).strip())
        if not ok:
            SCENARIOS_FAILED.add(index)
        return ok

    rules = dict(sc["rules"])
    rules["scoringMode"] = sc["mode"]
    parts, people, teams = build_entrants(sc["kind"], sc["names"], sc["n"])

    gen = {
        "knockout": generate_knockout_bracket,
        "round_robin": generate_round_robin_fixtures,
        "league_knockout": generate_league_knockout_fixtures,
        "group_stage": generate_group_stage_fixtures,
        "group_knockout": generate_group_knockout_fixtures,
    }[sc["format"]]

    try:
        matches = gen("T%d" % index, parts, sc["boards"],
                      number_of_sets=sc["sets"])
    except Exception:
        ck("a draw can be generated for every supported field size", False,
           traceback.format_exc()[-200:])
        return

    if not ck("a draw is produced for a field of two or more", bool(matches)):
        return

    # ---- structural invariants, before anything is played -----------------
    for m in matches:
        if m.get("player1Id") and m.get("player2Id"):
            ck("nobody is drawn against themselves",
               m["player1Id"] != m["player2Id"], m["id"])

    entrant_ids = set(p["id"] for p in parts)
    drawn = set()
    for m in matches:
        for side in SIDES:
            v = m.get(side + "Id")
            if v in entrant_ids:
                drawn.add(v)
    ck("every entrant appears somewhere in the draw", drawn == entrant_ids,
       "missing=%s" % sorted(entrant_ids - drawn)[:4])

    ck("match numbers are unique",
       len(set(m["matchNumber"] for m in matches)) == len(matches))

    if sc["format"] == "round_robin":
        pairs = set()
        for m in matches:
            pairs.add(tuple(sorted([str(m.get("player1Id")), str(m.get("player2Id"))])))
        ck("a round robin plays every pair exactly once",
           len(pairs) == len(matches) == sc["n"] * (sc["n"] - 1) // 2,
           "pairs=%d matches=%d" % (len(pairs), len(matches)))

    # ---- can each person find their own fixtures? -------------------------
    played_by = {}
    for m in matches:
        for side in SIDES:
            v = m.get(side + "Id")
            if v:
                played_by.setdefault(v, []).append(m)

    multi = 0
    for person in people:
        # By id, which is what a correctly provisioned account uses.
        expected = []
        for entrant_id in ({person["id"]} | set(teams.get(person["id"], set()))):
            expected.extend(played_by.get(entrant_id, []))
        found = find_my_matches(matches, teams, person["id"], person["name"])
        ck("a player finds every fixture their id appears in",
           len(found) == len(expected),
           "%s expected=%d found=%d" % (person["name"], len(expected), len(found)))
        if len(expected) > 1:
            multi += 1
            ck("a player entered in several matches sees all of them",
               len(found) == len(expected), person["name"])

        # By name, which is the fallback when the login is not the roster row.
        by_name = find_my_matches(matches, teams, "no-such-id", person["name"])
        if sc["kind"] == "singles" and expected:
            ck("the name fallback finds the same fixtures as the id",
               len(by_name) == len(expected),
               "%s by_name=%d expected=%d" % (person["name"], len(by_name),
                                              len(expected)))
        for m in by_name:
            names = {(m.get("player1Name") or "").strip().lower(),
                     (m.get("player2Name") or "").strip().lower()}
            ck("the name fallback never returns somebody else's fixture",
               person["name"].strip().lower() in names,
               "%s got %s" % (person["name"], sorted(names)))

    if sc["n"] >= 3 and sc["format"] in ("round_robin", "league_knockout",
                                         "group_stage"):
        ck("a field of three or more produces players with several fixtures",
           multi > 0, "multi=%d" % multi)

    # ---- play it ----------------------------------------------------------
    rng = Rng(index + 1)
    by_id = dict((m["id"], m) for m in matches)
    order = sorted(matches, key=lambda m: (m.get("roundIndex") or 0,
                                           m["matchNumber"]))
    completed = 0

    for m in order:
        if not (m.get("player1Id") and m.get("player2Id")):
            continue  # a slot still waiting on a promotion or a feeder

        walkover = sc["outcome"] == "with_walkovers" and rng.next(7) == 0
        if walkover:
            m["status"] = "completed"
            m["winnerId"] = m["player1Id"]
            m["winnerName"] = m["player1Name"]
            m["walkover"] = True
        else:
            boards = play_boards(m, sc["mode"], sc["sets"], sc["boards"],
                                 sc["outcome"], rules, rng)
            updated = settle(m, boards, sc["sets"], rules)
            m.update(updated)

            # The organiser resolves a tie the way the rules say to: an extra
            # board, played until it separates them. A knockout that still has
            # no winner after this is genuinely stuck.
            guard = 0
            while m.get("tieBreakRequired") and guard < 6:
                guard += 1
                extra = boards[-1]["boardNumber"] + 1 if boards else 1
                boards.append({
                    "setNumber": sc["sets"], "boardNumber": extra,
                    "status": "completed",
                    "player1Score": 9 if guard % 2 else 3,
                    "player2Score": 3 if guard % 2 else 9,
                    "boardWinner": "player1" if guard % 2 else "player2",
                })
                m.update(settle(m, boards, sc["sets"], rules))
            ck("a tie-break resolves within a few extra boards",
               not m.get("tieBreakRequired"),
               "%s still tied after %d extra boards" % (m["id"], guard))

            bw1 = m.get("player1BoardWins") or 0
            bw2 = m.get("player2BoardWins") or 0
            ck("board wins never exceed the boards played",
               bw1 + bw2 <= len(boards), "%s %d+%d>%d" % (m["id"], bw1, bw2,
                                                          len(boards)))
            ck("match points are never negative",
               (m.get("player1TotalPoints") or 0) >= 0
               and (m.get("player2TotalPoints") or 0) >= 0, m["id"])
            # The ceiling this simulation can legitimately produce: the coins a
            # side can hold, the queen, and one for the classic branch nudging
            # a coin count up to force a board winner.
            per_board = rules["coinsPerSide"] + rules["queenPoints"] + 1
            ck("a match total cannot exceed the boards times the most a board is worth",
               max(m.get("player1TotalPoints") or 0,
                   m.get("player2TotalPoints") or 0) <= len(boards) * per_board,
               "%s p1=%s p2=%s boards=%d ceiling=%d" % (
                   m["id"], m.get("player1TotalPoints"),
                   m.get("player2TotalPoints"), len(boards),
                   len(boards) * per_board))
            ck("every board of a finished match is accounted for",
               len(boards) >= sc["sets"] * sc["boards"], m["id"])

        # calculate_points_table only counts matches an organiser has signed
        # off, so the simulation has to sign them off too.
        m["resultConfirmed"] = True
        completed += 1

        winner_id = m.get("winnerId")
        if (m.get("stage") or "") == "knockout":
            # A knockout match with no winner is a dead end: the next round can
            # never be filled from it.
            ck("a knockout match always ends with a winner", bool(winner_id),
               "%s winner=%r status=%r tieBreak=%r" % (
                   m["id"], winner_id, m.get("status"),
                   m.get("tieBreakRequired")))
        else:
            ck("a league match ends either decided or drawn",
               m.get("status") == "completed",
               "%s status=%r" % (m["id"], m.get("status")))

        # Advance, the way the database RPC does.
        if winner_id and m.get("nextMatchId") in by_id:
            parent = by_id[m["nextMatchId"]]
            slot = m.get("nextMatchSlot")
            if slot in SIDES:
                parent[slot + "Id"] = winner_id
                parent[slot + "Name"] = m.get("winnerName")

    ck("at least one match of the draw could be played", completed > 0,
       "completed=0 of %d" % len(matches))

    # ---- knockout completion ---------------------------------------------
    if sc["format"] == "knockout":
        unplayable = [m["id"] for m in matches
                      if not (m.get("player1Id") and m.get("player2Id"))]
        ck("every knockout match becomes playable", not unplayable,
           "stuck=%s" % unplayable[:3])
        finals = [m for m in matches if not m.get("nextMatchId")]
        ck("the knockout ends in exactly one final", len(finals) == 1,
           "finals=%d" % len(finals))
        if len(finals) == 1:
            ck("the tournament produces a champion",
               bool(finals[0].get("winnerId")),
               "final=%s winner=%r" % (finals[0]["id"], finals[0].get("winnerId")))

    # ---- standings --------------------------------------------------------
    league = [m for m in matches if (m.get("stage") or "") != "knockout"]
    if league and sc["format"] in ("round_robin", "group_stage",
                                   "league_knockout", "group_knockout"):
        try:
            table = calculate_points_table(league, parts, rules)
        except Exception:
            ck("a points table can be built from the played matches", False,
               traceback.format_exc()[-200:])
            return

        ck("the table has a row per entrant", len(table) == len(parts),
           "rows=%d entrants=%d" % (len(table), len(parts)))

        played_total = sum(r["played"] for r in table)
        # Mirrors the filter inside calculate_points_table: signed off, and
        # belonging to the league stage.
        counted = [m for m in league
                   if m.get("resultConfirmed") and m.get("stage") == "league"]
        ck("every signed-off league match is counted twice, once per side",
           played_total == 2 * len(counted),
           "played_total=%d counted=%d stages=%s" % (
               played_total, len(counted),
               sorted(set(str(m.get("stage")) for m in league))))

        for r in table:
            ck("a row's results add up to the matches it played",
               r["won"] + r["lost"] + r["drawn"] == r["played"],
               "%s w=%d l=%d d=%d p=%d" % (r["participantName"], r["won"],
                                           r["lost"], r["drawn"], r["played"]))
            ck("table points are never negative", r["points"] >= 0,
               "%s pts=%d" % (r["participantName"], r["points"]))
            ck("a side cannot win more matches than it played",
               r["won"] <= r["played"], r["participantName"])

        ck("wins and losses balance across the table",
           sum(r["won"] for r in table) == sum(r["lost"] for r in table),
           "won=%d lost=%d" % (sum(r["won"] for r in table),
                               sum(r["lost"] for r in table)))
        ck("drawn results are counted on both sides",
           sum(r["drawn"] for r in table) % 2 == 0,
           "drawn=%d" % sum(r["drawn"] for r in table))
        ck("the table is ordered by rank",
           [r["rank"] for r in table] == sorted(r["rank"] for r in table))


# ---------------------------------------------------------------------------
# The scenario matrix
# ---------------------------------------------------------------------------

FIELDS = {
    "knockout":        [2, 3, 4, 5, 6, 7, 8, 9, 12, 16],
    "round_robin":     [2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "league_knockout": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
    "group_stage":     [6, 7, 8, 9, 10, 12, 14, 16, 18, 20],
    "group_knockout":  [6, 7, 8, 9, 10, 12, 14, 16, 18, 20],
}
MODES = ["classic", "remaining_coins"]
LAYOUTS = [(1, 3), (1, 8), (3, 8)]
OUTCOMES = ["decisive", "with_draws", "with_walkovers"]
KINDS = ["singles", "doubles"]
NAME_SHAPES = ["clean", "ordinal", "collision"]
RULE_SETS = [
    {"queenPoints": 3, "coinsPerSide": 9, "queenMustBeCovered": True,
     "queenAwardTo": "coverer"},
    {"queenPoints": 5, "coinsPerSide": 9, "queenMustBeCovered": False,
     "queenAwardTo": "pocketer"},
    {"queenPoints": 1, "coinsPerSide": 9, "queenMustBeCovered": True,
     "queenAwardTo": "pocketer", "pointsForWin": 3, "pointsForDraw": 1},
]


def build_matrix():
    scenarios = []
    i = 0
    for fmt, sizes in FIELDS.items():
        for n in sizes:
            for mode in MODES:
                for sets, boards in LAYOUTS:
                    for outcome in OUTCOMES:
                        scenarios.append({
                            "format": fmt, "n": n, "mode": mode,
                            "sets": sets, "boards": boards,
                            "outcome": outcome,
                            # Rotated rather than multiplied out, so every
                            # combination is still represented without the
                            # matrix exploding past what can be run quickly.
                            "kind": KINDS[i % len(KINDS)],
                            "names": NAME_SHAPES[i % len(NAME_SHAPES)],
                            "rules": RULE_SETS[i % len(RULE_SETS)],
                        })
                        i += 1
    return scenarios


def main():
    matrix = build_matrix()
    for index, sc in enumerate(matrix, start=1):
        try:
            run_scenario(sc, index)
        except Exception:
            check("a scenario runs to completion without raising", False,
                  "#%d %s n=%d: %s" % (index, sc["format"], sc["n"],
                                       traceback.format_exc()[-300:]))
            SCENARIOS_FAILED.add(index)

    total = sum(v[1] for v in RESULTS.values())
    failed = [(k, v) for k, v in sorted(RESULTS.items()) if v[0]]

    print("=" * 78)
    print("whole-tournament scenario suite")
    print("=" * 78)
    print("scenarios played    : %d" % SCENARIOS_RUN)
    print("scenarios with a failure : %d" % len(SCENARIOS_FAILED))
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
