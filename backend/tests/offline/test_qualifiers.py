"""
Qualification: who a league table sends through, and where they land.

Every table used to flag its top four whatever the draw -- four "qualified" in a
group of three, four promised a place in a two-slot final -- and promotion read
one flat list, the first category's, so a doubles bracket was filled from the
singles table. These cases pin the rule the draw was actually built with:

    - groups:            qualifiersPerGroup from EACH group
    - league -> knockout: as many as the first knockout round seats
    - a plain league:    the legacy four, unchanged
    - singles + doubles: each bracket filled from its own table

Technique: draws come from the real fixture engine and are written to the
in-memory database the way POST /tournaments/{id}/fixtures writes them, then
the league is decided in a known order so the table is known in advance. The
standings are read over HTTP; promotion is driven both through the endpoint
and through the hook the confirm route calls.

    python tests/offline/test_qualifiers.py
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness                                   # noqa: E402
from app.services.fixture_engine import (                     # noqa: E402
    generate_group_knockout_fixtures, generate_league_knockout_fixtures,
    generate_round_robin_fixtures,
)
from app.services.scoring_engine import calculate_points_table   # noqa: E402
from app.services.qualification import (                      # noqa: E402
    promote_qualifiers, try_auto_promote, slot_is_waiting,
)
from app.routers.standings import compute_standings           # noqa: E402

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


# ---------------------------------------------------------------------------
# Building a tournament in the fake, the way the app leaves one
# ---------------------------------------------------------------------------

def singles_entrants(h, tid, count, prefix="Solo"):
    """`count` approved singles players; the engine's participant objects."""
    pool = []
    for i in range(count):
        pid = h.make_user("%s %d" % (prefix, i + 1))
        h.db.seed("registrations", [{
            "id": "reg-%s-%d" % (prefix.lower(), i), "tournament_id": tid,
            "type": "singles", "player_id": pid, "status": "approved",
        }])
        pool.append({"id": pid, "name": "%s %d" % (prefix, i + 1), "rating": 1500 + i})
    return pool


def doubles_entrants(h, tid, count):
    """`count` approved doubles teams, each of two real profiles."""
    pool = []
    for i in range(count):
        a = h.make_user("Pair %d A" % (i + 1))
        b = h.make_user("Pair %d B" % (i + 1))
        team_id = "team-%d" % (i + 1)
        team = {"id": team_id, "name": "Pair %d" % (i + 1), "player1_id": a,
                "player2_id": b, "rating": 1500 + i}
        h.db.seed("teams", [team])
        h.db.seed("registrations", [{
            "id": "reg-pair-%d" % i, "tournament_id": tid,
            "type": "doubles", "team_id": team_id, "status": "approved",
        }])
        pool.append(team)
    return pool


def seed_draw(h, tid, fixtures):
    """
    Write an engine draw the way POST /tournaments/{id}/fixtures does: one
    snake_case row per match, then the bracket links once every id exists.
    """
    h.db.seed("matches", [{
        "id": m["id"], "tournament_id": tid,
        "match_number": m["matchNumber"], "round_name": m["roundName"],
        "round_index": m["roundIndex"], "stage": m["stage"], "type": m["type"],
        "player1_id": m.get("player1Id"), "player2_id": m.get("player2Id"),
        "player1_name": m["player1Name"], "player2_name": m["player2Name"],
        "status": "scheduled", "max_boards": m["maxBoards"], "target_points": 29,
        "result_confirmed": False,
        "player1_board_wins": 0, "player2_board_wins": 0,
        "player1_total_points": 0, "player2_total_points": 0,
        "bracket_position": m.get("bracketPosition"),
    } for m in fixtures])
    for m in fixtures:
        if m.get("nextMatchId"):
            h.db.table("matches").update({
                "next_match_id": m["nextMatchId"],
                "next_match_slot": m.get("nextMatchSlot"),
            }).eq("id", m["id"]).execute()


def decide_league(h, tid, strength, category=None):
    """
    Confirm every league match, the side earlier in `strength` winning, so the
    table comes out in `strength` order and the test knows who should go
    through. `category` limits it to one competition.
    """
    order = {pid: i for i, pid in enumerate(strength)}
    for m in h.db.tables["matches"]:
        if m["tournament_id"] != tid or m.get("stage") != "league":
            continue
        if category and (m.get("type") or "singles") != category:
            continue
        p1, p2 = m["player1_id"], m["player2_id"]
        p1_wins = order.get(p1, 10 ** 6) < order.get(p2, 10 ** 6)
        m.update({
            "status": "completed", "result_confirmed": True,
            "winner_id": p1 if p1_wins else p2,
            "winner_name": m["player1_name"] if p1_wins else m["player2_name"],
            "player1_board_wins": 2 if p1_wins else 1,
            "player2_board_wins": 1 if p1_wins else 2,
            "player1_total_points": 20 if p1_wins else 10,
            "player2_total_points": 10 if p1_wins else 20,
        })


def standings_of(h, tid):
    r = h.get("/api/standings/%s" % tid)
    check("the standings can be read", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    return body(r) if r.status_code == 200 else {}


def block_for(result, category):
    return next((c for c in result.get("categories", []) if c.get("category") == category), {})


def qualified_in(rows):
    return [row for row in rows if row.get("isQualified")]


def knockout_rows(h, tid, category):
    return [m for m in h.db.rows("matches")
            if m["tournament_id"] == tid and m.get("stage") == "knockout"
            and (m.get("type") or "singles") == category]


def first_round(rows):
    fed = {m.get("next_match_id") for m in rows if m.get("next_match_id")}
    return [m for m in rows if m["id"] not in fed]


# ---------------------------------------------------------------------------
# Groups: qualifiersPerGroup from each group, not four from the first
# ---------------------------------------------------------------------------

def test_groups_honour_qualifiers_per_group():
    cases = (
        # format alone asks for groups
        ("group_knockout", {"groupCount": 3, "qualifiersPerGroup": 2}, 2),
        # groupCount on a league format asks for them too, and the cut is not
        # a hard-coded two either
        ("league_knockout", {"groupCount": 3, "qualifiersPerGroup": 1}, 1),
        # the snake_case spelling the model saves under
        ("group_knockout", {"group_count": 3, "qualifiers_per_group": 2}, 2),
    )
    for fmt, extra, per_group in cases:
        ctx = "format=%s rules=%s" % (fmt, extra)
        h = Harness()
        admin = h.make_user("Admin", "admin")
        tid = h.seed_tournament(owner_id=admin, format=fmt, rules=dict(RULES, **extra))
        pool = singles_entrants(h, tid, 9)
        fixtures = generate_group_knockout_fixtures(
            tid, pool, 3, group_count=3, qualifiers_per_group=per_group)
        seed_draw(h, tid, fixtures)
        decide_league(h, tid, [p["id"] for p in pool])

        result = standings_of(h, tid)
        block = block_for(result, "singles")
        groups = block.get("groups") or []
        if not check("a group draw is reported as three groups", len(groups) == 3,
                     "%s groups=%d" % (ctx, len(groups))):
            continue

        for g in groups:
            rows = g.get("standings") or []
            top = qualified_in(rows)
            check("exactly qualifiersPerGroup entrants are marked qualified in each group",
                  len(top) == per_group,
                  "%s group %s qualified=%d want=%d" % (ctx, g.get("group"), len(top), per_group))
            check("the qualifiers are the top of their group",
                  [row.get("rank") for row in top] == list(range(1, per_group + 1)),
                  "%s group %s ranks=%s" % (ctx, g.get("group"), [row.get("rank") for row in top]))
            check("a group never flags more qualifiers than it has members",
                  len(top) <= len(rows), "%s group %s" % (ctx, g.get("group")))
            check("each group reports the cut it applied",
                  g.get("qualifyingCount") == per_group, "%s %s" % (ctx, g.get("qualifyingCount")))

        check("the category's flat table carries every group's qualifiers and no more",
              len(qualified_in(block.get("standings") or [])) == 3 * per_group,
              "%s flat qualified=%d" % (ctx, len(qualified_in(block.get("standings") or []))))
        check("the category reports the per-group cut",
              block.get("qualifyingCount") == per_group, "%s %s" % (ctx, block.get("qualifyingCount")))

        # The seats the draw made must match what the table says goes through.
        seats = sum(1 for m in fixtures if m["stage"] == "knockout"
                    for s in ("player1", "player2") if m[s + "Name"].startswith("Group "))
        check("the qualifiers flagged equal the labelled seats in the bracket",
              seats == 3 * per_group, "%s seats=%d" % (ctx, seats))

        # /qualified without a count follows the same rule.
        r = h.get("/api/standings/%s/qualified" % tid)
        check("the qualified list can be read without asking for a number",
              r.status_code == 200, "%s %s %s" % (ctx, r.status_code, detail(r)))
        if r.status_code == 200:
            listed = block_for(body(r), "singles").get("qualified") or []
            check("the qualified list without a count is the configured cut per group",
                  len(listed) == 3 * per_group, "%s listed=%d" % (ctx, len(listed)))
            per = {}
            for row in listed:
                per[row.get("group")] = per.get(row.get("group"), 0) + 1
            check("the qualified list draws evenly from every group",
                  set(per.values()) == {per_group}, "%s %s" % (ctx, per))

        # With a count, a group draw is cut per group rather than off the top
        # of the flat list -- which would have been all of group A.
        r = h.get("/api/standings/%s/qualified?count=1" % tid)
        if check("the qualified list can be read with a count", r.status_code == 200,
                 "%s %s %s" % (ctx, r.status_code, detail(r))):
            listed = block_for(body(r), "singles").get("qualified") or []
            check("a count is applied to each group's table",
                  sorted(row.get("group") for row in listed) == ["A", "B", "C"],
                  "%s %s" % (ctx, [row.get("group") for row in listed]))


def test_append_group_knockout_uses_group_ranks():
    """An existing played group league gets one seat per actual group rank."""
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin, format="group_stage",
                            rules=dict(RULES, groupCount=3, qualifiersPerGroup=2))
    pool = singles_entrants(h, tid, 9)
    league = [m for m in generate_group_knockout_fixtures(
        tid, pool, 3, group_count=3, qualifiers_per_group=2)
              if m["stage"] == "league"]
    seed_draw(h, tid, league)
    decide_league(h, tid, [p["id"] for p in pool])
    before_ids = {m["id"] for m in h.db.rows("matches")}

    wrong = h.post("/api/fixtures/%s/knockout?slots=8" % tid, user_id=admin)
    check("group bracket rejects a cut that disagrees with its groups",
          wrong.status_code == 422 and "use 6 knockout slots" in detail(wrong), detail(wrong))

    response = h.post("/api/fixtures/%s/knockout?slots=6" % tid, user_id=admin)
    if not check("six group qualifiers can enter an appended bracket",
                 response.status_code == 200, "%s %s" % (response.status_code, detail(response))):
        return
    check("adding the group bracket preserves played league matches",
          before_ids.issubset({m["id"] for m in h.db.rows("matches")}))
    bracket = knockout_rows(h, tid, "singles")
    check("six qualifiers generate five knockout matches", len(bracket) == 5, len(bracket))
    # Promotion resolves labels throughout the bracket, including bye slots
    # placed directly into the semi-final by the bracket engine.
    seated = [m.get(key) for m in bracket for key in ("player1_id", "player2_id")
              if m.get(key) in {p["id"] for p in pool}]
    check("every group qualifier has one distinct bracket seat",
          len(seated) == len(set(seated)) == 6, seated)
    check("all six seats come from the top two of their group",
          set(seated) == {row["participantId"] for group in
                          block_for(standings_of(h, tid), "singles")["groups"]
                          for row in group["standings"][:2]}, seated)


def test_force_cannot_reseed_played_knockout():
    """Early league promotion never grants consent to rewrite played knockouts."""
    for status, confirmed in (("live", False), ("paused", False),
                              ("completed", False), ("scheduled", True)):
        h = Harness()
        admin = h.make_user("Admin", "admin")
        tid = h.seed_tournament(owner_id=admin, format="league_knockout",
                                rules=dict(RULES))
        pool = singles_entrants(h, tid, 8)
        seed_draw(h, tid, generate_league_knockout_fixtures(tid, pool, 3))
        knockout = knockout_rows(h, tid, "singles")
        first = first_round(knockout)[0]
        h.db.table("matches").update({"status": status,
                                     "result_confirmed": confirmed}).eq("id", first["id"]).execute()
        before = [(m["id"], m.get("player1_id"), m.get("player2_id"))
                  for m in knockout_rows(h, tid, "singles")]
        response = h.post("/api/standings/%s/promote?force=true" % tid, {}, user_id=admin)
        check("force cannot re-seed a knockout with play already recorded",
              response.status_code == 409, "%s %s %s" % (status, confirmed, detail(response)))
        after = [(m["id"], m.get("player1_id"), m.get("player2_id"))
                 for m in knockout_rows(h, tid, "singles")]
        check("a rejected forced promotion changes no knockout seats",
              after == before, "%s %s" % (status, after))


# ---------------------------------------------------------------------------
# A league feeding a knockout: as many as the first round seats
# ---------------------------------------------------------------------------

def build_league_knockout(h, admin, entrants, fmt="league_knockout"):
    tid = h.seed_tournament(owner_id=admin, format=fmt, rules=dict(RULES))
    pool = singles_entrants(h, tid, entrants)
    fixtures = generate_league_knockout_fixtures(tid, pool, 3)
    seed_draw(h, tid, fixtures)
    return tid, pool, fixtures


def test_league_knockout_marks_the_bracket_size():
    # 8 entrants draw a four-seat knockout; 5 draw a two-seat final. Neither
    # is the legacy four in the second case, which is the point.
    for entrants, expected in ((8, 4), (5, 2)):
        ctx = "entrants=%d" % entrants
        h = Harness()
        admin = h.make_user("Admin", "admin")
        tid, pool, fixtures = build_league_knockout(h, admin, entrants)
        decide_league(h, tid, [p["id"] for p in pool])

        seats = {m[s + "Name"] for m in fixtures if m["stage"] == "knockout"
                 for s in ("player1", "player2") if m[s + "Name"].startswith("League Rank")}
        check("the fixture engine drew the expected number of seats",
              len(seats) == expected, "%s seats=%s" % (ctx, sorted(seats)))

        block = block_for(standings_of(h, tid), "singles")
        top = qualified_in(block.get("standings") or [])
        check("a league feeding a knockout flags as many qualifiers as the bracket seats",
              len(top) == expected, "%s qualified=%d want=%d" % (ctx, len(top), expected))
        check("the qualifiers are the top of the table",
              [row.get("rank") for row in top] == list(range(1, expected + 1)),
              "%s ranks=%s" % (ctx, [row.get("rank") for row in top]))
        check("the category reports the bracket-sized cut",
              block.get("qualifyingCount") == expected, "%s %s" % (ctx, block.get("qualifyingCount")))
        check("a league-knockout has no groups", block.get("groups") == [], ctx)

        # Promotion replaces the labels with names; the cut must not change
        # underneath the table once it has.
        r = h.post("/api/standings/%s/promote" % tid, {}, user_id=admin)
        check("the bracket can be promoted", r.status_code == 200,
              "%s %s %s" % (ctx, r.status_code, detail(r)))
        after = qualified_in(block_for(standings_of(h, tid), "singles").get("standings") or [])
        check("the cut is unchanged after promotion has filled the labels",
              len(after) == expected, "%s qualified=%d" % (ctx, len(after)))


# ---------------------------------------------------------------------------
# Nothing configured: the legacy four
# ---------------------------------------------------------------------------

def test_legacy_default_stays_four():
    parts = [{"id": "p%d" % i, "name": "P%d" % i} for i in range(6)]

    rows = calculate_points_table([], parts, {})
    check("a caller that says nothing gets the top four flagged",
          len(qualified_in(rows)) == 4, len(qualified_in(rows)))
    check("the legacy four are ranks one to four",
          [r["rank"] for r in qualified_in(rows)] == [1, 2, 3, 4],
          [r["rank"] for r in qualified_in(rows)])

    for cut in (0, 1, 2, 6, 9):
        rows = calculate_points_table([], parts, {}, qualifying_count=cut)
        check("the caller's cut is honoured exactly",
              len(qualified_in(rows)) == min(cut, len(parts)),
              "cut=%d flagged=%d" % (cut, len(qualified_in(rows))))
    rows = calculate_points_table([], parts, {}, qualifying_count=None)
    check("an unspecified cut is the legacy four", len(qualified_in(rows)) == 4)

    # A plain round robin over HTTP: no groups, no knockout, four qualify.
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin, format="round_robin", rules=dict(RULES))
    pool = singles_entrants(h, tid, 6)
    seed_draw(h, tid, generate_round_robin_fixtures(tid, pool, 3))
    decide_league(h, tid, [p["id"] for p in pool])

    block = block_for(standings_of(h, tid), "singles")
    check("a plain league still flags its top four",
          len(qualified_in(block.get("standings") or [])) == 4,
          len(qualified_in(block.get("standings") or [])))
    check("a plain league reports the legacy cut", block.get("qualifyingCount") == 4,
          block.get("qualifyingCount"))


# ---------------------------------------------------------------------------
# Singles and doubles side by side: each bracket from its own table
# ---------------------------------------------------------------------------

def build_mixed(h, admin):
    tid = h.seed_tournament(owner_id=admin, format="league_knockout", rules=dict(RULES))
    singles = singles_entrants(h, tid, 8)
    doubles = doubles_entrants(h, tid, 8)
    drawn = []
    for category, pool in (("singles", singles), ("doubles", doubles)):
        fixtures = generate_league_knockout_fixtures(tid, pool, 3, id_prefix=category[0])
        for m in fixtures:
            m["type"] = category
        drawn.extend(fixtures)
    # One continuous numbering across both categories, as the route does.
    for i, m in enumerate(drawn, start=1):
        m["matchNumber"] = i
    seed_draw(h, tid, drawn)
    return tid, [p["id"] for p in singles], [t["id"] for t in doubles]


def check_bracket(h, tid, category, pool_ids, ctx):
    rows = first_round(knockout_rows(h, tid, category))
    filled = [m.get(s + "_id") for m in rows for s in ("player1", "player2")]
    check("every first-round seat of the %s bracket is filled" % category,
          rows and all(filled), "%s %s" % (ctx, filled))
    check("the %s bracket is filled from the %s table" % (category, category),
          all(pid in pool_ids for pid in filled if pid), "%s %s" % (ctx, filled))
    check("no rank label survives promotion in the %s bracket" % category,
          not any(slot_is_waiting(m, s) for m in rows for s in ("player1", "player2")),
          "%s %s" % (ctx, [(m.get("player1_name"), m.get("player2_name")) for m in rows]))
    check("the %s seats go to the top of the %s table" % (category, category),
          set(pid for pid in filled if pid) == set(pool_ids[:4]),
          "%s got=%s want=%s" % (ctx, sorted(str(p) for p in filled), sorted(pool_ids[:4])))


def bracket_waiting(h, tid, category):
    return any(slot_is_waiting(m, s) for m in knockout_rows(h, tid, category)
               for s in ("player1", "player2"))


def test_mixed_categories_promote_into_both_brackets():
    # Through the endpoint.
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid, singles, teams = build_mixed(h, admin)
    decide_league(h, tid, singles + teams)

    result = standings_of(h, tid)
    check("a mixed tournament has a table per category",
          sorted(c.get("category") for c in result.get("categories", [])) == ["doubles", "singles"],
          [c.get("category") for c in result.get("categories", [])])
    check("the doubles table ranks teams, not people",
          all(row.get("participantId") in teams
              for row in block_for(result, "doubles").get("standings") or []),
          [row.get("participantName") for row in block_for(result, "doubles").get("standings") or []])

    r = h.post("/api/standings/%s/promote" % tid, {}, user_id=admin)
    check("promotion succeeds on a mixed tournament", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    payload = body(r) if r.status_code == 200 else {}
    check("promotion fills both brackets' seats", payload.get("promotedCount") == 8,
          payload.get("promotedCount"))
    check("nothing is left unresolved", payload.get("unresolved") == [], payload.get("unresolved"))
    check("the summary says which category each seat belongs to",
          sorted({p.get("category") for p in payload.get("promoted") or []}) == ["doubles", "singles"],
          [p.get("category") for p in payload.get("promoted") or []])
    check_bracket(h, tid, "singles", singles, "endpoint")
    check_bracket(h, tid, "doubles", teams, "endpoint")

    # Through the hook the confirm route calls.
    h2 = Harness()
    admin2 = h2.make_user("Admin", "admin")
    tid2, singles2, teams2 = build_mixed(h2, admin2)
    decide_league(h2, tid2, singles2 + teams2)
    auto = try_auto_promote(h2.db, tid2)
    check("auto-promotion runs when both leagues are complete",
          bool(auto) and auto.get("promotedCount") == 8, auto)
    check_bracket(h2, tid2, "singles", singles2, "auto")
    check_bracket(h2, tid2, "doubles", teams2, "auto")
    check("a second pass finds nothing waiting", try_auto_promote(h2.db, tid2) is None)

    # One league finishing does not wait for the other.
    h3 = Harness()
    admin3 = h3.make_user("Admin", "admin")
    tid3, singles3, teams3 = build_mixed(h3, admin3)
    decide_league(h3, tid3, singles3, category="singles")
    partial = try_auto_promote(h3.db, tid3)
    check("a finished singles league is promoted while doubles is still playing",
          bool(partial) and partial.get("promotedCount") == 4, partial)
    check_bracket(h3, tid3, "singles", singles3, "partial")
    check("the doubles bracket waits for its own league",
          bracket_waiting(h3, tid3, "doubles"))
    decide_league(h3, tid3, teams3, category="doubles")
    rest = try_auto_promote(h3.db, tid3)
    check("the doubles bracket is filled once its league finishes",
          bool(rest) and rest.get("promotedCount") == 4, rest)
    check_bracket(h3, tid3, "doubles", teams3, "partial")


def test_flat_standings_still_promote():
    """The pre-category shape -- a bare list of rows -- must keep working."""
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid, pool, _fixtures = build_league_knockout(h, admin, 8)
    decide_league(h, tid, [p["id"] for p in pool])
    rows = compute_standings(h.db, tid).get("standings", [])
    matches = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    result = promote_qualifiers(h.db, tid, rows, matches)
    check("a bare list of rows promotes as before", result.get("promotedCount") == 4, result)
    check_bracket(h, tid, "singles", [p["id"] for p in pool], "flat")




# ---------------------------------------------------------------------------
# Appending a knockout stage to a league that has already been played
#
# A round robin drawn as `round_robin` has no bracket at all, and the only
# other route to one -- regenerating fixtures -- deletes every match, board and
# score in the tournament. These cases pin the property that matters: the
# league survives untouched, and the bracket it gets is the one the standings
# can be promoted into.
# ---------------------------------------------------------------------------

def test_knockout_stage_appends_without_touching_the_league():
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin, format="round_robin", rules=RULES)
    pool = singles_entrants(h, tid, 20)
    league = generate_round_robin_fixtures(tid, pool, 3)
    seed_draw(h, tid, league)
    decide_league(h, tid, [p["id"] for p in pool])

    before = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    before_ids = {m["id"] for m in before}
    before_confirmed = sum(1 for m in before if m.get("result_confirmed"))

    r = h.post("/api/fixtures/%s/knockout?slots=8" % tid, user_id=admin)
    if not check("a knockout stage can be appended to a played league",
                 r.status_code == 200, "%s %s" % (r.status_code, detail(r))):
        return
    payload = body(r)

    # The whole point: nothing that was already played is disturbed.
    after = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    survivors = [m for m in after if m["id"] in before_ids]
    check("appending a bracket deletes no existing match",
          len(survivors) == len(before), "%d of %d survived" % (len(survivors), len(before)))
    check("appending a bracket un-confirms no existing result",
          sum(1 for m in survivors if m.get("result_confirmed")) == before_confirmed,
          "confirmed %d want %d" % (sum(1 for m in survivors if m.get("result_confirmed")),
                                    before_confirmed))
    check("the league keeps every one of its matches",
          len([m for m in after if m.get("stage") == "league"]) == len(league),
          "league=%d want=%d" % (len([m for m in after if m.get("stage") == "league"]), len(league)))

    rows = knockout_rows(h, tid, "singles")
    check("eight slots draw seven knockout matches", len(rows) == 7, len(rows))
    check("the endpoint reports what it drew", payload.get("matchesCreated") == 7, payload)
    check("the rounds drawn are the quarter-final onward",
          sorted({m["round_name"] for m in rows}) == ["Final", "Quarter Final", "Semi Final"],
          sorted({m["round_name"] for m in rows}))
    check("four matches make up the first knockout round",
          len(first_round(rows)) == 4, len(first_round(rows)))

    # Numbering and rounds continue past the league rather than colliding with it.
    league_rows = [m for m in after if m.get("stage") == "league"]
    check("knockout match numbers continue past the league",
          min(m["match_number"] for m in rows) > max(m["match_number"] for m in league_rows),
          "ko from %d, league to %d" % (min(m["match_number"] for m in rows),
                                        max(m["match_number"] for m in league_rows)))
    check("the knockout rounds sit after the last league round",
          min(m["round_index"] for m in rows) > max(m["round_index"] for m in league_rows),
          "ko r%d, league r%d" % (min(m["round_index"] for m in rows),
                                  max(m["round_index"] for m in league_rows)))

    # Every knockout match is the same length as the matches that fed it.
    check("a knockout match is drawn to the same board count as the league",
          {m["max_boards"] for m in rows} == {league[0]["maxBoards"]},
          {m["max_boards"] for m in rows})

    # The cut is read back off the bracket, so the table now flags eight.
    block = block_for(standings_of(h, tid), "singles")
    check("the bracket teaches the table to flag eight qualifiers",
          block.get("qualifyingCount") == 8, block.get("qualifyingCount"))
    top = qualified_in(block.get("standings") or [])
    check("exactly the top eight are flagged as qualified",
          [row.get("rank") for row in top] == list(range(1, 9)),
          [row.get("rank") for row in top])


def test_appended_bracket_seeds_itself_from_the_standings():
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin, format="round_robin", rules=RULES)
    pool = singles_entrants(h, tid, 20)
    seed_draw(h, tid, generate_round_robin_fixtures(tid, pool, 3))
    strength = [p["id"] for p in pool]
    decide_league(h, tid, strength)

    r = h.post("/api/fixtures/%s/knockout?slots=8" % tid, user_id=admin)
    if not check("a decided league seeds the bracket as it is drawn",
                 r.status_code == 200 and body(r).get("leagueComplete") is True,
                 "%s %s" % (r.status_code, detail(r))):
        return

    rows = knockout_rows(h, tid, "singles")
    names = {p["id"]: p["name"] for p in pool}
    seeded = {}
    for m in first_round(rows):
        for slot in ("player1", "player2"):
            check("no quarter-final slot is left holding a rank label",
                  not slot_is_waiting(m, slot), "%s %s" % (m["round_name"], m[slot + "_name"]))
            if m[slot + "_id"]:
                seeded[m[slot + "_id"]] = m["id"]

    check("the eight quarter-finalists are the league's top eight",
          set(seeded) == set(strength[:8]),
          [names.get(pid) for pid in seeded])
    check("nobody outside the top eight reaches the bracket",
          not (set(seeded) & set(strength[8:])), [names.get(pid) for pid in seeded])

    # Seeding, not registration order: rank 1 must not meet rank 2 before the final.
    pairs = {frozenset((m["player1_id"], m["player2_id"])) for m in first_round(rows)}
    expected = {frozenset((strength[a - 1], strength[b - 1]))
                for a, b in ((1, 8), (4, 5), (2, 7), (3, 6))}
    check("the quarter-finals are drawn 1v8, 2v7, 3v6, 4v5", pairs == expected,
          [[names.get(p) for p in pair] for pair in pairs])

    # Later rounds wait for winners, and the final is fed by both semis.
    later = [m for m in rows if m["id"] not in {x["id"] for x in first_round(rows)}]
    check("the rounds after the first wait on winners",
          all(m["player1_id"] is None and m["player2_id"] is None for m in later),
          [(m["round_name"], m["player1_name"]) for m in later])
    final = [m for m in rows if m["round_name"] == "Final"]
    check("exactly one match is fed by nothing else", len(final) == 1, len(final))
    check("both semi-finals feed the final",
          sorted(m["next_match_slot"] for m in rows if m.get("next_match_id") == final[0]["id"])
          == ["player1", "player2"],
          [m["next_match_slot"] for m in rows if m.get("next_match_id") == final[0]["id"]])


def test_appending_a_bracket_is_guarded():
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin, format="round_robin", rules=RULES)
    pool = singles_entrants(h, tid, 20)
    seed_draw(h, tid, generate_round_robin_fixtures(tid, pool, 3))
    decide_league(h, tid, [p["id"] for p in pool])

    r = h.post("/api/fixtures/%s/knockout?slots=20" % tid, user_id=admin)
    check("a manual top 20 draw succeeds", r.status_code == 200, detail(r))
    rows = knockout_rows(h, tid, "singles")
    check("the top 20 draw includes nineteen matches", len(rows) == 19, len(rows))
    promoted_ids = [m.get(slot + "_id") for m in rows for slot in ("player1", "player2") if m.get(slot + "_id")]
    check("promotion fills all twenty qualifiers including byes",
          sorted(promoted_ids) == sorted(p["id"] for p in pool), promoted_ids)
    drawn = len(knockout_rows(h, tid, "singles"))

    # Asking twice must not silently stack a second bracket on the first.
    r = h.post("/api/fixtures/%s/knockout?slots=8" % tid, user_id=admin)
    check("a second draw is refused rather than stacked", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))
    check("the refused draw added nothing",
          len(knockout_rows(h, tid, "singles")) == drawn,
          len(knockout_rows(h, tid, "singles")))

    # replace=true redraws, but only while the bracket is unplayed.
    r = h.post("/api/fixtures/%s/knockout?slots=4&replace=true" % tid, user_id=admin)
    check("an unplayed bracket can be redrawn to a different size",
          r.status_code == 200 and body(r).get("matchesReplaced") == drawn,
          "%s %s" % (r.status_code, detail(r)))
    check("redrawing leaves only the new bracket",
          len(knockout_rows(h, tid, "singles")) == 3,
          len(knockout_rows(h, tid, "singles")))

    # Once a knockout match has been played, a redraw would delete a result.
    ko = knockout_rows(h, tid, "singles")[0]
    h.db.table("matches").update({"status": "completed", "result_confirmed": True}).eq(
        "id", ko["id"]).execute()
    r = h.post("/api/fixtures/%s/knockout?slots=8&replace=true" % tid, user_id=admin)
    check("a played bracket cannot be redrawn even with replace", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))


def test_bracket_waits_when_the_league_is_unfinished():
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin, format="round_robin", rules=RULES)
    pool = singles_entrants(h, tid, 20)
    seed_draw(h, tid, generate_round_robin_fixtures(tid, pool, 3))
    decide_league(h, tid, [p["id"] for p in pool])

    # Leave one league result open, the way a no-show leaves one open.
    open_match = next(m for m in h.db.rows("matches")
                      if m["tournament_id"] == tid and m.get("stage") == "league")
    h.db.table("matches").update({"status": "scheduled", "result_confirmed": False}).eq(
        "id", open_match["id"]).execute()

    r = h.post("/api/fixtures/%s/knockout?slots=8" % tid, user_id=admin)
    if not check("an unfinished league still gets its bracket drawn",
                 r.status_code == 200, "%s %s" % (r.status_code, detail(r))):
        return
    payload = body(r)
    check("the draw reports the league as unfinished",
          payload.get("leagueComplete") is False, payload.get("leagueConfirmed"))
    check("an unfinished league leaves the slots unseeded",
          payload.get("promotion") is None, payload.get("promotion"))

    rows = knockout_rows(h, tid, "singles")
    check("the slots keep their rank labels until the league is decided",
          all(slot_is_waiting(m, "player1") for m in first_round(rows)),
          [m["player1_name"] for m in first_round(rows)])

    # Confirming the last league result is what fills them -- the hook the
    # confirm route already calls, not a second manual step.
    h.db.table("matches").update({
        "status": "completed", "result_confirmed": True,
        "winner_id": open_match["player1_id"],
        "winner_name": open_match["player1_name"],
        "player1_board_wins": 2, "player2_board_wins": 1,
        "player1_total_points": 20, "player2_total_points": 10,
    }).eq("id", open_match["id"]).execute()
    promotion = try_auto_promote(h.db, tid)
    check("confirming the last league result promotes the qualifiers",
          bool(promotion) and promotion.get("promotedCount") == 8, promotion)
    check("every quarter-final slot is resolved once the league is decided",
          not any(slot_is_waiting(m, s) for m in first_round(knockout_rows(h, tid, "singles"))
                  for s in ("player1", "player2")),
          [m["player1_name"] for m in first_round(knockout_rows(h, tid, "singles"))])




# ---------------------------------------------------------------------------
# How big the knockout is, is the organiser's decision
#
# generate_league_knockout_fixtures computed its bracket as
# `min(4, max(2, len(participants) // 2))` -- a HARD CEILING of four. A league
# of twenty, described as "top 8 move to the quarter-finals", still drew a
# four-slot bracket, and no setting anywhere could change it. These cases pin
# the rule that replaced it.
# ---------------------------------------------------------------------------

def test_the_knockout_is_sized_by_the_rule():
    pool = [{"id": "p%d" % i, "name": "P%d" % i, "rating": 1500 + i} for i in range(20)]

    cases = (
        # (rule value, expected seats, expected first round name)
        (None, 4, "Semi Final"),     # unchanged default, so old draws redraw the same
        (2, 2, "Final"),
        (4, 4, "Semi Final"),
        (8, 8, "Quarter Final"),
        (16, 16, "Round of 16"),
    )
    for value, seats, first_name in cases:
        drawn = generate_league_knockout_fixtures(
            "T", pool, 3, knockout_qualifiers=value)
        ko = [m for m in drawn if m["stage"] == "knockout"]
        labelled = [s for m in ko for s in ("player1", "player2")
                    if str(m[s + "Name"]).startswith("League Rank")]
        check("a knockout of %s has %d labelled seats" % (value, seats),
              len(labelled) == seats,
              "%s -> %d seats" % (value, len(labelled)))

        fed = {m.get("nextMatchId") for m in ko if m.get("nextMatchId")}
        opening = [m for m in ko if m["id"] not in fed]
        check("a knockout of %s opens with the %s" % (value, first_name),
              all(m["roundName"] == first_name for m in opening),
              sorted({m["roundName"] for m in opening}))

        check("a knockout of %s leaves exactly one final" % value,
              len([m for m in ko if m["roundName"] == "Final"]) == 1,
              [m["roundName"] for m in ko])

        # The league is untouched by the knockout's size: everyone still plays
        # everyone, whatever the bracket takes.
        league = [m for m in drawn if m["stage"] == "league"]
        check("the league is a full round robin whatever the bracket takes",
              len(league) == len(pool) * (len(pool) - 1) // 2,
              "%s -> %d league matches" % (value, len(league)))


def test_manual_knockout_counts_preserve_every_qualifier():
    pool = [{"id": "p%d" % i, "name": "P%d" % i, "rating": 1500 + i} for i in range(40)]
    for asked in (3, 6, 10, 12, 20, 31):
        ko = [m for m in generate_league_knockout_fixtures(
            "T", pool, 3, knockout_qualifiers=asked) if m["stage"] == "knockout"]
        ranks = [int(m[slot + "Name"].split("#")[1]) for m in ko
                 for slot in ("player1", "player2")
                 if m[slot + "Name"].startswith("League Rank")]
        check("manual count preserves every qualifier once", sorted(ranks) == list(range(1, asked + 1)), ranks)
        check("manual count draws exactly n minus one matches", len(ko) == asked - 1, len(ko))
        check("manual count leaves no fabricated player IDs",
              all(m[slot + "Id"] is None for m in ko for slot in ("player1", "player2")), ranks)
        if asked == 20:
            check("top 20 has four preliminary matches", sum(m["roundName"] == "Round of 32" for m in ko) == 4, ko)
            check("top 20 still reaches four quarterfinal matches", sum(m["roundName"] == "Quarter Final" for m in ko) == 4, ko)


def test_a_bracket_cannot_be_bigger_than_the_field():
    """
    Eight seats need eight players. A bracket with more seats than entrants
    leaves "League Rank #9" in a field of eight, and promotion can never
    resolve it -- the slot sits unfilled and the round cannot be played.
    """
    for entrants, asked, seats in ((8, 16, 8), (5, 8, 5), (3, 8, 3), (2, 16, 2)):
        pool = [{"id": "p%d" % i, "name": "P%d" % i, "rating": 1500 + i}
                for i in range(entrants)]
        ko = [m for m in generate_league_knockout_fixtures(
            "T", pool, 3, knockout_qualifiers=asked) if m["stage"] == "knockout"]
        labelled = [s for m in ko for s in ("player1", "player2")
                    if str(m[s + "Name"]).startswith("League Rank")]
        check("a field of %d asked for %d seats draws %d" % (entrants, asked, seats),
              len(labelled) == seats,
              "%d entrants, asked %d, got %d" % (entrants, asked, len(labelled)))

        ranks = [int(str(m[s + "Name"]).split("#")[1])
                 for m in ko for s in ("player1", "player2")
                 if str(m[s + "Name"]).startswith("League Rank")]
        check("no seat asks for a rank the field cannot supply",
              all(r <= entrants for r in ranks), sorted(ranks))


def test_the_rule_reaches_the_draw_through_the_api():
    """
    End to end: the number an organiser picks on the create form is the number
    of quarter-finalists, and the points table then flags exactly that many.
    """
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = h.seed_tournament(owner_id=admin, format="league_knockout",
                            rules=dict(RULES, knockoutQualifiers=8))
    pool = singles_entrants(h, tid, 12)

    drawn = generate_league_knockout_fixtures(
        tid, pool, 3, knockout_qualifiers=8)
    seed_draw(h, tid, drawn)
    decide_league(h, tid, [p["id"] for p in pool])

    rows = knockout_rows(h, tid, "singles")
    check("the drawn bracket has four quarter-finals",
          len([m for m in rows if m["round_name"] == "Quarter Final"]) == 4,
          [m["round_name"] for m in rows])

    block = block_for(standings_of(h, tid), "singles")
    check("the table reads the cut back off the bracket and flags eight",
          block.get("qualifyingCount") == 8, block.get("qualifyingCount"))
    top = qualified_in(block.get("standings") or [])
    check("exactly the top eight are marked qualified",
          [r.get("rank") for r in top] == list(range(1, 9)),
          [r.get("rank") for r in top])

    # And promotion fills them from the table, in seeded order.
    promote_qualifiers(h.db, tid, standings_of(h, tid),
                       [m for m in h.db.rows("matches") if m["tournament_id"] == tid])
    filled = first_round(knockout_rows(h, tid, "singles"))
    check("every quarter-final slot is resolved to a real entrant",
          not any(slot_is_waiting(m, s) for m in filled for s in ("player1", "player2")),
          [(m["player1_name"], m["player2_name"]) for m in filled])

    strength = [p["id"] for p in pool]
    seeded = {pid for m in filled for pid in (m["player1_id"], m["player2_id"]) if pid}
    check("the eight quarter-finalists are the league's top eight",
          seeded == set(strength[:8]), len(seeded))




# ---------------------------------------------------------------------------
# Head-to-head: the tiebreaker the app advertises
# ---------------------------------------------------------------------------

def _h2h_match(n, p1, p2, w, b1, b2, s1, s2):
    return {"id": "h%d" % n, "stage": "league", "resultConfirmed": True,
            "player1Id": p1, "player2Id": p2, "winnerId": w,
            "player1BoardWins": b1, "player2BoardWins": b2,
            "player1TotalPoints": s1, "player2TotalPoints": s2}


def test_nsd_is_the_default_first_tiebreaker_and_can_be_reordered():
    parts = [{"id": pid, "name": pid.upper()} for pid in ("a", "b", "c", "d")]
    matches = [
        _h2h_match(1, "a", "b", "a", 3, 0, 20, 19),
        _h2h_match(2, "c", "a", "c", 3, 2, 20, 19),
        _h2h_match(3, "d", "b", "d", 2, 1, 29, 10),
        _h2h_match(4, "c", "d", "c", 2, 1, 20, 10),
    ]
    rows = calculate_points_table(matches, parts, {})
    by_id = {r["participantId"]: r for r in rows}
    check("NSD is points scored minus points conceded",
          by_id["d"]["scoreFor"] == 39
          and by_id["d"]["scoreAgainst"] == 30
          and by_id["d"]["scoreDiff"] == 9, by_id["d"])
    check("equal match points rank by NSD before board difference by default",
          by_id["a"]["points"] == by_id["d"]["points"] == 2
          and by_id["a"]["boardDiff"] > by_id["d"]["boardDiff"]
          and by_id["d"]["rank"] < by_id["a"]["rank"],
          (by_id["a"], by_id["d"]))

    configured = calculate_points_table(matches, parts, {
        "tiebreakerRules": ["points", "board_difference",
                             "net_score_difference", "head_to_head"],
    })
    ranks = {r["participantId"]: r["rank"] for r in configured}
    check("an explicit board difference first rule is honoured",
          ranks["a"] < ranks["d"], ranks)


def test_head_to_head_separates_entrants_no_column_can():
    """
    Both the points table and the create form promise organisers that a tie
    is settled by "Head-to-Head match outcome". It never ran --
    rules.tiebreakerRules stored the word and no ranking code read it -- so
    two entrants level on every counted column were separated ALPHABETICALLY.
    The player who won the meeting could be ranked below the player they beat,
    and in a league feeding a knockout that decides who goes through.
    """
    parts = [{"id": "adam", "name": "Adam"}, {"id": "zara", "name": "Zara"},
             {"id": "carl", "name": "Carl"}, {"id": "dave", "name": "Dave"}]
    rules = {"pointsForWin": 2, "pointsForDraw": 1, "pointsForLoss": 0}

    matches = [
        # Zara beats Adam.
        _h2h_match(1, "adam", "zara", "zara", 1, 2, 18, 20),
        # Then Zara loses to Dave and Adam beats Carl by the SAME margins, so
        # points, wins, board difference, net score difference and score for
        # all come out identical.
        _h2h_match(2, "zara", "dave", "dave", 1, 2, 18, 20),
        _h2h_match(3, "adam", "carl", "adam", 2, 1, 20, 18),
    ]

    rows = calculate_points_table(matches, parts, rules)
    by_id = {r["participantId"]: r for r in rows}
    adam, zara = by_id["adam"], by_id["zara"]

    def numeric(r):
        return (r["points"], r["won"], r["boardDiff"], r["scoreDiff"], r["scoreFor"])

    if not check("the two entrants really are level on every counted column",
                 numeric(adam) == numeric(zara),
                 "%s vs %s" % (numeric(adam), numeric(zara))):
        return

    check("the entrant who won the meeting is ranked above the one who lost it",
          zara["rank"] < adam["rank"],
          "Zara #%s, Adam #%s" % (zara["rank"], adam["rank"]))
    check("alphabetical order no longer decides a head-to-head",
          zara["rank"] < adam["rank"], "Adam #%s" % adam["rank"])


def test_a_head_to_head_cycle_falls_back_rather_than_looping():
    """
    Beating somebody is not transitive. A beats B, B beats C, C beats A is a
    real result, and no ordering satisfies it -- so the mini-league scores
    them equal and the existing stable order stands. What must not happen is
    a crash or a non-deterministic answer.
    """
    parts = [{"id": "a", "name": "Ann"}, {"id": "b", "name": "Bea"},
             {"id": "c", "name": "Cal"}]
    rules = {"pointsForWin": 2, "pointsForDraw": 1, "pointsForLoss": 0}
    matches = [
        _h2h_match(1, "a", "b", "a", 2, 1, 20, 18),
        _h2h_match(2, "b", "c", "b", 2, 1, 20, 18),
        _h2h_match(3, "c", "a", "c", 2, 1, 20, 18),
    ]

    first = calculate_points_table(matches, parts, rules)
    second = calculate_points_table(matches, parts, rules)
    check("a cycle still produces a full table", len(first) == 3, len(first))
    check("a cycle produces the same order twice",
          [r["participantId"] for r in first] == [r["participantId"] for r in second],
          [r["participantId"] for r in first])
    check("every entrant in a cycle keeps a distinct rank",
          sorted(r["rank"] for r in first) == [1, 2, 3],
          [r["rank"] for r in first])


def test_a_cut_that_covers_the_field_flags_nobody():
    """
    A plain league takes the legacy cut of four, so a league of three or four
    flagged EVERY row as qualified -- including last place, told they had gone
    through to a knockout that does not exist. A cut covering the whole field
    is not a cut. A cut the caller supplied is still honoured as given.
    """
    parts = [{"id": "p%d" % i, "name": "P%d" % i} for i in range(3)]
    rows = calculate_points_table([], parts, {})
    check("a three-entrant plain league flags nobody as qualified",
          not any(r["isQualified"] for r in rows),
          [(r["participantName"], r["isQualified"]) for r in rows])

    rows = calculate_points_table([], parts, {}, qualifying_count=2)
    check("an explicit cut inside the field is still applied",
          sum(1 for r in rows if r["isQualified"]) == 2,
          sum(1 for r in rows if r["isQualified"]))


def test_seeded_knockout_keeps_its_league_table_locked():
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid, pool, _ = build_league_knockout(h, admin, 4)

    early = h.post("/api/standings/%s/promote?force=true" % tid, {}, user_id=admin)
    check("an unfinished league cannot be seeded even with force",
          early.status_code == 409 and "Early seeding" in detail(early), detail(early))
    check("early promotion leaves rank labels waiting",
          bracket_waiting(h, tid, "singles"))

    decide_league(h, tid, [p["id"] for p in pool])
    seeded = h.post("/api/standings/%s/promote" % tid, {}, user_id=admin)
    check("the completed league can fill the knockout",
          seeded.status_code == 200 and body(seeded).get("promotedCount", 0) > 0,
          "%s %s" % (seeded.status_code, detail(seeded)))
    seats_before = [(m["id"], m.get("player1_id"), m.get("player2_id"))
                    for m in knockout_rows(h, tid, "singles")]
    league = next(m for m in h.db.rows("matches") if m["tournament_id"] == tid
                  and m.get("stage") == "league")

    reopen = h.post("/api/matches/%s/reopen" % league["id"],
                    {"reason": "score was transposed"}, user_id=admin)
    check("a seeded league result cannot be reopened",
          reopen.status_code == 409 and "knockout" in detail(reopen).lower(), detail(reopen))
    correction = h.put("/api/matches/%s/boards/1" % league["id"],
                       {"boardNumber": 1}, user_id=admin)
    check("a seeded league board cannot be corrected",
          correction.status_code == 409, detail(correction))
    re_pair = h.put("/api/matches/%s?force=true" % league["id"],
                    {"player1Id": pool[-1]["id"]}, user_id=admin)
    check("force cannot re-pair a seeded league result",
          re_pair.status_code == 409, detail(re_pair))
    deleted = h.delete("/api/matches/%s?force=true" % league["id"], user_id=admin)
    check("force cannot delete a seeded league result",
          deleted.status_code == 409, detail(deleted))
    match_count = len(h.db.rows("matches"))
    late_league = h.post("/api/tournaments/%s/matches" % tid, {
        "stage": "league", "player1Id": pool[0]["id"], "player2Id": pool[1]["id"],
    }, user_id=admin)
    check("a seeded knockout refuses another league fixture",
          late_league.status_code == 409, detail(late_league))
    check("the refused late league fixture adds no match",
          len(h.db.rows("matches")) == match_count)
    check("rejected changes preserve the confirmed table and knockout seats",
          league.get("result_confirmed") is True and seats_before == [
              (m["id"], m.get("player1_id"), m.get("player2_id"))
              for m in knockout_rows(h, tid, "singles")])

    mixed = Harness()
    mixed_admin = mixed.make_user("Mixed Admin", "admin")
    mixed_tid, singles, _teams = build_mixed(mixed, mixed_admin)
    decide_league(mixed, mixed_tid, singles, category="singles")
    try_auto_promote(mixed.db, mixed_tid)
    doubles_match = next(m for m in mixed.db.rows("matches")
                         if m["tournament_id"] == mixed_tid and m.get("stage") == "league"
                         and m.get("type") == "doubles")
    mixed.db.table("matches").update({
        "result_confirmed": True, "status": "completed",
    }).eq("id", doubles_match["id"]).execute()
    unseeded = mixed.post("/api/matches/%s/reopen" % doubles_match["id"],
                          {"reason": "correct a doubles score"}, user_id=mixed_admin)
    check("singles seeding does not lock an unrelated doubles league",
          unseeded.status_code == 200, detail(unseeded))




def _opening_and_byes(entrants, groups, per_group):
    """(same-group opening pairs, qualifiers who got a bye) for one draw."""
    pool = [{"id": "p%d" % i, "name": "P%d" % i, "rating": 1500 + i}
            for i in range(entrants)]
    drawn = generate_group_knockout_fixtures(
        "T", pool, 3, group_count=groups, qualifiers_per_group=per_group)
    ko = [m for m in drawn if m["stage"] == "knockout"]
    fed = {m.get("nextMatchId") for m in ko if m.get("nextMatchId")}
    opening = [m for m in ko if m["id"] not in fed]

    same = [(m["player1Name"], m["player2Name"]) for m in opening
            if str(m["player1Name"]).startswith("Group ")
            and str(m["player2Name"]).startswith("Group ")
            and str(m["player1Name"]).split(" #")[0]
            == str(m["player2Name"]).split(" #")[0]]

    in_round_one = {n for m in opening for n in (m["player1Name"], m["player2Name"])
                    if str(n).startswith("Group ")}
    every_qualifier = {name for m in ko for name in (m["player1Name"], m["player2Name"])
                       if str(name).startswith("Group ")}
    return same, sorted(every_qualifier - in_round_one)


def test_a_bye_goes_to_a_group_winner_never_a_runner_up():
    """
    Separating groupmates must not re-order the tiers.

    The first version of _separate_groupmates swapped placeholders by seed
    alone, and the seed is what decides who gets a bye. With 3 groups of 3
    and two through, the byes went to Group B's WINNER and Group C's
    RUNNER-UP, while Group A #1 and Group C #1 -- two group winners -- were
    made to play each other in round one. Somebody who finished second in
    their group rested into the semi-final over two who finished first.

    Swapping only within a rank tier keeps every tier where it was, so the
    byes stay on the lowest seed numbers, which are the winners.
    """
    cases = ((9, 3, 2), (14, 7, 2), (12, 4, 2), (20, 4, 2),
             (15, 5, 2), (21, 7, 3), (10, 5, 2))
    for entrants, groups, per_group in cases:
        ctx = "%d entrants, %d groups, top %d" % (entrants, groups, per_group)
        same, byes = _opening_and_byes(entrants, groups, per_group)

        check("no qualifier meets a groupmate in the opening round",
              not same, "%s -> %s" % (ctx, same))

        runners_with_a_bye = [b for b in byes if not b.endswith("#1")]
        check("a bye never goes to anyone but a group winner",
              not runners_with_a_bye, "%s -> %s" % (ctx, runners_with_a_bye))


def test_separating_groupmates_keeps_every_qualifier_exactly_once():
    """
    The swap moves seeds around; it must not lose or duplicate anybody.

    Worth pinning separately: a swap that wrote one placeholder's seed onto
    another without moving the second would silently drop a qualifier from
    the bracket, and the draw would still look plausible.
    """
    for entrants, groups, per_group in ((9, 3, 2), (14, 7, 2), (16, 4, 4), (12, 3, 2)):
        ctx = "%d entrants, %d groups, top %d" % (entrants, groups, per_group)
        pool = [{"id": "p%d" % i, "name": "P%d" % i, "rating": 1500 + i}
                for i in range(entrants)]
        ko = [m for m in generate_group_knockout_fixtures(
            "T", pool, 3, group_count=groups, qualifiers_per_group=per_group)
            if m["stage"] == "knockout"]

        seats = [name for m in ko for name in (m["player1Name"], m["player2Name"])
                 if str(name).startswith("Group ")]
        check("every qualifier appears exactly once in the bracket",
              len(seats) == len(set(seats)), "%s -> %s" % (ctx, sorted(seats)))

        expected = {"Group %s #%d" % (chr(65 + g), r)
                    for g in range(groups) for r in range(1, per_group + 1)}
        # The engine clamps per_group to the smallest group, so the expected
        # set is an upper bound; what matters is that nobody appears who was
        # never a qualifier.
        check("no seat names a qualifier the groups cannot supply",
              set(seats) <= expected, "%s -> %s" % (ctx, sorted(set(seats) - expected)))


SUITES = [
    ("groups", test_groups_honour_qualifiers_per_group),
    ("append group knockout", test_append_group_knockout_uses_group_ranks),
    ("played knockouts cannot be reseeded", test_force_cannot_reseed_played_knockout),
    ("league knockout", test_league_knockout_marks_the_bracket_size),
    ("legacy default", test_legacy_default_stays_four),
    ("mixed categories", test_mixed_categories_promote_into_both_brackets),
    ("flat standings", test_flat_standings_still_promote),
    ("appended knockout", test_knockout_stage_appends_without_touching_the_league),
    ("appended seeding", test_appended_bracket_seeds_itself_from_the_standings),
    ("append guards", test_appending_a_bracket_is_guarded),
    ("append waits", test_bracket_waits_when_the_league_is_unfinished),
    ("knockout size is a rule", test_the_knockout_is_sized_by_the_rule),
    ("manual qualifier counts", test_manual_knockout_counts_preserve_every_qualifier),
    ("bracket fits the field", test_a_bracket_cannot_be_bigger_than_the_field),
    ("the rule reaches the draw", test_the_rule_reaches_the_draw_through_the_api),
    ("NSD tiebreaker order", test_nsd_is_the_default_first_tiebreaker_and_can_be_reordered),
    ("head-to-head decides", test_head_to_head_separates_entrants_no_column_can),
    ("head-to-head cycle", test_a_head_to_head_cycle_falls_back_rather_than_looping),
    ("a cut covering the field", test_a_cut_that_covers_the_field_flags_nobody),
    ("byes go to group winners", test_a_bye_goes_to_a_group_winner_never_a_runner_up),
    ("no qualifier is lost", test_separating_groupmates_keeps_every_qualifier_exactly_once),
    ("seeded league result lock", test_seeded_knockout_keeps_its_league_table_locked),
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
    print("qualifiers suite (real app, in-memory database)")
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
