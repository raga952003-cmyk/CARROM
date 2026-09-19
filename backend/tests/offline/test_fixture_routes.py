"""
The fixture routes: POST /fixtures/{id}/generate and the autoGenerate path of
POST /imports/confirm, over HTTP, against the in-memory database.

Both reach the draw through routers/tournaments.generate_fixtures, whose
signature is (id, force, admin). Both called it as (id, admin), so the admin
profile slid into `force` and `admin` kept FastAPI's Depends marker -- every
draw through these two routes was a 400 that named no cause. Nothing caught
it, because every other suite draws through POST /tournaments/{id}/fixtures,
which FastAPI wires correctly. These cases pin the routes themselves.

Technique: BLACK BOX along the organiser's path (approve a pool, draw, retry,
redraw), with WHITE BOX cases at the branches identified by reading the code:
the idempotency replay and its different-body refusal, the `force` switch in
front of the recorded-results guard, autoGenerate off unless asked, the
fixtureError the import reports, and each arm of require_tournament_access.

    python tests/offline/test_fixture_routes.py
"""
import json
import os
import sys
import traceback
import uuid

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

# What an umpire records on one board; enough to count as play.
SCORE = {"p1Score": 0, "p2Score": 0, "setNumber": 1, "boardWinner": "player1",
         "coinsRemainingWith": "player2", "coinsRemaining": 5,
         "queenPocketedBy": "none", "queenCoveredBy": "none"}


def tournament_payload(fmt, boards):
    return {
        "name": "Fixture Routes %s" % fmt, "description": "", "category": "singles",
        "format": fmt,
        "registrationStartDate": "2026-01-01", "registrationEndDate": "2026-02-01",
        "tournamentStartDate": "2026-03-01", "tournamentEndDate": "2026-03-02",
        "venue": "Hall A", "city": "Chennai",
        "numberOfBoards": boards, "entryFee": 0,
        "rules": dict(RULES), "status": "draft",
    }


def create_tournament(h, admin, fmt="round_robin", boards=3):
    """Through the API, so the row carries everything the scheduler reads."""
    r = h.post("/api/tournaments", tournament_payload(fmt, boards), user_id=admin)
    if not check("a tournament can be created for the draw", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return None
    return body(r).get("id")


def approve_pool(h, admin, tid, entrants, prefix="Entrant"):
    """Register `entrants` singles players and approve them, as the desk would."""
    for i in range(entrants):
        rp = h.post("/api/players", {"name": "%s %d" % (prefix, i + 1),
                                     "email": "%s%d@carrom.example.com" % (prefix.lower(), i),
                                     "rating": 1500 + i}, user_id=admin)
        if not check("every entrant can be added", rp.status_code == 200, detail(rp)):
            return False
        h.post("/api/tournaments/%s/registrations" % tid,
               {"type": "singles", "playerId": body(rp).get("id")}, user_id=admin)

    regs = body(h.get("/api/tournaments/%s/registrations" % tid, admin))
    for reg in (regs if isinstance(regs, list) else []):
        if reg.get("status") == "pending":
            h.post("/api/registrations/%s/approve" % reg["id"], {}, user_id=admin)
    approved = [r for r in h.db.rows("registrations")
                if r["tournament_id"] == tid and r.get("status") == "approved"]
    return check("the pool is approved before the draw", len(approved) == entrants,
                 "approved=%d of %d" % (len(approved), entrants))


def matches_of(h, tid):
    return [m for m in h.db.rows("matches") if m["tournament_id"] == tid]


def match_ids(h, tid):
    return sorted(m["id"] for m in matches_of(h, tid))


def confirm_import(h, who, tid, entries, auto_generate=None):
    """POST /imports/confirm exactly as the browser sends it: a form, not JSON."""
    form = {"tournamentId": tid, "players_json": json.dumps(entries)}
    if auto_generate is not None:
        form["autoGenerate"] = "true" if auto_generate else "false"
    return h.client.post("/api/imports/confirm", data=form, headers=h.auth(who))


# ---------------------------------------------------------------------------
# POST /fixtures/{id}/generate -- the draw, its retry, and its redraw
# ---------------------------------------------------------------------------

def test_generate_draws_replays_and_redraws():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=3)
    if not tid or not approve_pool(h, admin, tid, 6):
        return

    headers = dict(h.auth(admin))
    headers["Idempotency-Key"] = "draw-once"

    first = h.client.post("/api/fixtures/%s/generate" % tid, headers=headers)
    if not check("the fixtures route draws an approved pool", first.status_code == 200,
                 "%s %s" % (first.status_code, detail(first))):
        return
    answer = body(first)
    check("the draw reports what it drew",
          answer.get("status") == "success"
          and (answer.get("byCategory") or {}).get("singles") == 15, answer)

    drawn = matches_of(h, tid)
    check("a six-entrant round robin drawn through /fixtures has fifteen matches",
          len(drawn) == 15, "matches=%d" % len(drawn))
    ids = {m["id"] for m in drawn}
    boards = [b for b in h.db.rows("boards") if b["match_id"] in ids]
    check("every drawn match carries its boards", len(boards) == 15 * 3,
          "boards=%d" % len(boards))
    check("every drawn match names both sides",
          all(m.get("player1_id") and m.get("player2_id") for m in drawn),
          [(m.get("player1_name"), m.get("player2_name")) for m in drawn[:3]])
    first_ids = match_ids(h, tid)

    # ---- the read side ------------------------------------------------
    r = h.client.get("/api/fixtures/%s" % tid)
    listed = body(r)
    check("the fixtures list is readable without signing in",
          r.status_code == 200 and isinstance(listed, list) and len(listed) == 15,
          "%s %s" % (r.status_code, str(listed)[:120]))
    if isinstance(listed, list) and listed:
        check("a listed fixture carries its boards",
              all(len(m.get("boards") or []) == 3 for m in listed),
              [len(m.get("boards") or []) for m in listed[:5]])
    r = h.client.get("/api/fixtures/%s?stage=league&round_index=1" % tid)
    round_one = body(r)
    check("the fixtures list filters by stage and round",
          r.status_code == 200 and isinstance(round_one, list) and len(round_one) == 3,
          "%s %s" % (r.status_code, str(round_one)[:120]))
    r = h.client.get("/api/fixtures/%s?stage=knockout" % tid)
    check("a round robin has no knockout fixtures to list",
          r.status_code == 200 and body(r) == [], "%s %s" % (r.status_code, body(r)))

    # ---- the retry ----------------------------------------------------
    second = h.client.post("/api/fixtures/%s/generate" % tid, headers=headers)
    check("a retried draw with the same key is accepted",
          second.status_code == 200, "%s %s" % (second.status_code, detail(second)))
    check("a retried draw replays the first answer", body(second) == answer,
          "first=%s second=%s" % (str(answer)[:120], str(body(second))[:120]))
    check("a retried draw does not redraw", match_ids(h, tid) == first_ids,
          "matches changed under a replayed key")

    # Same key, different request: the key that drew cautiously is not consent
    # to discard results.
    third = h.client.post("/api/fixtures/%s/generate?force=true" % tid, headers=headers)
    check("reusing a key with a different request is refused",
          third.status_code == 409 and "Idempotency-Key" in detail(third),
          "%s %s" % (third.status_code, detail(third)))
    check("a refused replay leaves the draw alone", match_ids(h, tid) == first_ids,
          "matches changed under a refused key")

    # ---- the redraw ---------------------------------------------------
    # The verdict on a plain redraw is deliberately not pinned. generate_fixtures
    # opens board 1 of every match for play as it writes the draw, and its
    # results guard then counts every board that is not `pending` as play
    # recorded -- so even an untouched draw is refused without force, with a
    # message about results that do not exist. That is a defect in
    # routers/tournaments.py, not in this route. What this route owes is that
    # the request reaches the guard at all, and that `force` gets through it.
    r = h.post("/api/fixtures/%s/generate" % tid, user_id=admin)
    check("a plain redraw reaches the draw, whatever the guard decides",
          r.status_code in (200, 409) and "Depends" not in detail(r),
          "%s %s" % (r.status_code, detail(r)))
    if r.status_code == 409:
        check("a refused redraw says what would be lost", "Regenerating" in detail(r),
              detail(r))
        check("a refused redraw keeps the existing draw", match_ids(h, tid) == first_ids,
              "matches changed under a refused redraw")
    current = match_ids(h, tid)

    # Record play, and the guard must stand until force says otherwise.
    target = matches_of(h, tid)[0]
    rs = h.post("/api/matches/%s/boards/1/submit" % target["id"], SCORE, user_id=admin)
    if check("a board on the drawn match can be scored", rs.status_code == 200,
             "%s %s" % (rs.status_code, detail(rs))):
        r = h.post("/api/fixtures/%s/generate" % tid, user_id=admin)
        check("a redraw over recorded play is refused without force",
              r.status_code == 409 and "Regenerating" in detail(r),
              "%s %s" % (r.status_code, detail(r)))
        check("the refused redraw keeps the scored draw", match_ids(h, tid) == current,
              "matches changed under a refused redraw")

        r = h.post("/api/fixtures/%s/generate?force=true" % tid, user_id=admin)
        check("force redraws over recorded play", r.status_code == 200,
              "%s %s" % (r.status_code, detail(r)))
        forced = match_ids(h, tid)
        check("a forced redraw replaces the scored draw",
              len(forced) == 15 and not set(forced) & set(current),
              "matches=%d overlap=%d" % (len(forced), len(set(forced) & set(current))))
        fresh = [b for b in h.db.rows("boards") if b["match_id"] in set(forced)]
        check("a forced redraw starts from blank boards",
              len(fresh) == 15 * 3 and not any((b.get("player1_score") or 0)
                                               or (b.get("player2_score") or 0)
                                               for b in fresh),
              "boards=%d scored=%d" % (len(fresh), sum(
                  1 for b in fresh if (b.get("player1_score") or 0)
                  or (b.get("player2_score") or 0))))

    # ---- the edges ----------------------------------------------------
    r = h.post("/api/fixtures/%s/generate" % uuid.uuid4(), user_id=admin)
    check("drawing a tournament that does not exist is a 404", r.status_code == 404,
          "%s %s" % (r.status_code, detail(r)))

    thin = create_tournament(h, admin, "knockout", boards=2)
    if thin and approve_pool(h, admin, thin, 1, prefix="Lone"):
        r = h.post("/api/fixtures/%s/generate" % thin, user_id=admin)
        check("a pool of one is refused with a reason, not a stack trace",
              r.status_code == 400 and "fewer than 2" in detail(r),
              "%s %s" % (r.status_code, detail(r)))


# ---------------------------------------------------------------------------
# POST /imports/confirm with autoGenerate -- the other path into the draw
# ---------------------------------------------------------------------------

def test_import_confirm_autogenerate():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid:
        return

    entries = [{"name": "Imported %d" % (i + 1),
                "email": "imported%d@carrom.example.com" % i,
                "type": "singles", "rating": 1500 + i,
                "club": "Riverside", "city": "Chennai"} for i in range(4)]

    r = confirm_import(h, admin, tid, entries, auto_generate=True)
    if not check("a confirmed import with autoGenerate is accepted", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return
    p = body(r)
    check("the import registers every row",
          p.get("imported") == 4 and p.get("singlesImported") == 4 and p.get("skipped") == [],
          p)
    check("autoGenerate builds the fixtures", p.get("fixturesGenerated") is True, p)
    check("autoGenerate reports no fixture error",
          p.get("fixtureError") is None and "not generated" not in str(p.get("message")),
          p)
    check("the import says the draw was made and the schedule published",
          "Fixtures generated" in str(p.get("message")), p.get("message"))

    drawn = matches_of(h, tid)
    check("a four-entrant import draws six matches", len(drawn) == 6,
          "matches=%d" % len(drawn))
    check("the auto-generated draw is scheduled",
          drawn and all(m.get("scheduled_time") and m.get("scheduled_date") for m in drawn),
          [(m.get("scheduled_date"), m.get("scheduled_time")) for m in drawn[:3]])
    check("the auto-generated draw stays within the venue's boards",
          drawn and all(1 <= (m.get("board_number") or 0) <= 2 for m in drawn),
          sorted(set(m.get("board_number") for m in drawn)))

    row = [t for t in h.db.rows("tournaments") if t["id"] == tid][0]
    check("the tournament records the draw and the published schedule",
          row.get("fixtures_generated") is True and row.get("schedule_published") is True,
          {k: row.get(k) for k in ("fixtures_generated", "schedule_published")})

    participants = {r_["player_id"] for r_ in h.db.rows("registrations")
                    if r_["tournament_id"] == tid and r_.get("status") == "approved"}
    check("every imported entrant is registered and approved", len(participants) == 4,
          "approved=%d" % len(participants))
    told = {n.get("profile_id") for n in h.db.rows("notifications")
            if n.get("tournament_id") == tid and n.get("type") == "schedule_published"}
    check("every imported entrant is told the schedule is published",
          participants <= told, "missing=%s" % sorted(participants - told))

    profiles = {p_["email"] for p_ in h.db.rows("profiles")}
    check("an imported entrant has a profile row",
          all(e["email"] in profiles for e in entries),
          [e["email"] for e in entries if e["email"] not in profiles])

    # ---- off unless asked ---------------------------------------------
    before = match_ids(h, tid)
    late = [{"name": "Late Entrant", "email": "late@carrom.example.com", "type": "singles"}]
    r = confirm_import(h, admin, tid, late)
    check("a later import without the flag is accepted", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    p = body(r)
    check("an import does not rebuild the draw unless asked",
          p.get("fixturesGenerated") is False and p.get("fixtureError") is None
          and match_ids(h, tid) == before, p)
    approved = [r_ for r_ in h.db.rows("registrations")
                if r_["tournament_id"] == tid and r_.get("status") == "approved"]
    check("the late entrant is registered all the same", len(approved) == 5,
          "approved=%d" % len(approved))

    # A second import of the same people adds nobody and draws nothing.
    r = confirm_import(h, admin, tid, entries, auto_generate=True)
    p = body(r)
    check("re-importing the same sheet registers nobody twice",
          r.status_code == 200 and p.get("imported") == 0, p)
    check("re-importing the same sheet leaves the draw alone",
          p.get("fixturesGenerated") is False and match_ids(h, tid) == before, p)

    # ---- when the draw cannot be built, the reason is machine-readable -------
    thin = create_tournament(h, admin, "knockout", boards=2)
    if thin:
        r = confirm_import(h, admin, thin, [
            {"name": "Only One", "email": "only.one@carrom.example.com", "type": "singles"},
        ], auto_generate=True)
        p = body(r)
        check("an import whose draw cannot be built still imports", r.status_code == 200
              and p.get("imported") == 1, "%s %s" % (r.status_code, p))
        check("an import whose draw cannot be built says why in fixtureError",
              p.get("fixturesGenerated") is False
              and "fewer than 2" in str(p.get("fixtureError")), p)
        check("the fixture failure is a reason, not a Python error",
              "Depends" not in str(p.get("fixtureError"))
              and "object" not in str(p.get("fixtureError")), p.get("fixtureError"))
        check("the fixture failure also reaches the message",
              "not generated" in str(p.get("message")), p.get("message"))
        check("a failed draw writes no matches", not matches_of(h, thin),
              "matches=%d" % len(matches_of(h, thin)))


# ---------------------------------------------------------------------------
# Who may draw, and who may import
# ---------------------------------------------------------------------------

def test_access_boundaries():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    other = h.make_user("Other Admin", "admin")
    scorer = h.make_user("Scorer", "admin")
    player = h.make_user("A Player", "player")
    tid = create_tournament(h, owner, "round_robin", boards=2)
    if not tid or not approve_pool(h, owner, tid, 4):
        return
    h.db.seed("tournament_access", [{
        "id": "acc-1", "tournament_id": tid, "user_id": scorer,
        "access_role": "scorer", "status": "approved", "decided_by": owner,
    }])
    sheet = [{"name": "Gatecrasher", "email": "gate@carrom.example.com", "type": "singles"}]

    for who, label in ((other, "another admin"), (scorer, "an approved scorer"),
                       (player, "a player")):
        r = h.post("/api/fixtures/%s/generate" % tid, user_id=who)
        check("%s cannot draw a tournament they do not manage" % label,
              r.status_code == 403, "%s %s" % (r.status_code, detail(r)))
        check("a refused draw is a reason, not a Python error",
              "Depends" not in detail(r) and "{" not in detail(r), detail(r))
        check("a refused draw writes no matches", not matches_of(h, tid),
              "matches=%d" % len(matches_of(h, tid)))

        r = confirm_import(h, who, tid, sheet, auto_generate=True)
        check("%s cannot import into a tournament they do not manage" % label,
              r.status_code == 403, "%s %s" % (r.status_code, detail(r)))
        regs = [x for x in h.db.rows("registrations") if x["tournament_id"] == tid]
        check("a refused import registers nobody", len(regs) == 4, "registrations=%d" % len(regs))

    r = h.post("/api/fixtures/%s/generate" % tid, user_id=other)
    check("the refusal names the owner so access can be requested",
          "Owner" in detail(r), detail(r))
    r = h.post("/api/fixtures/%s/generate" % tid, user_id=scorer)
    check("a scorer is told the draw is the owner's to make",
          "owner" in detail(r).lower(), detail(r))

    r = h.client.post("/api/fixtures/%s/generate" % tid)
    check("an anonymous caller cannot draw", r.status_code == 401,
          "%s %s" % (r.status_code, detail(r)))
    r = h.client.post("/api/imports/confirm",
                      data={"tournamentId": tid, "players_json": json.dumps(sheet)})
    check("an anonymous caller cannot import", r.status_code == 401,
          "%s %s" % (r.status_code, detail(r)))

    r = h.post("/api/fixtures/%s/generate" % tid, user_id=owner)
    check("the owner can draw their own tournament", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    check("the owner's draw is written", len(matches_of(h, tid)) == 6,
          "matches=%d" % len(matches_of(h, tid)))

    # A manager the owner approved stands in for them. A second event, because
    # the first already has its draw and a redraw would meet the results guard.
    tid_b = create_tournament(h, owner, "round_robin", boards=2)
    if tid_b and approve_pool(h, owner, tid_b, 4, prefix="Second"):
        h.db.seed("tournament_access", [{
            "id": "acc-2", "tournament_id": tid_b, "user_id": scorer,
            "access_role": "manager", "status": "approved", "decided_by": owner,
        }])
        r = h.post("/api/fixtures/%s/generate" % tid_b, user_id=scorer)
        check("an approved manager can draw", r.status_code == 200,
              "%s %s" % (r.status_code, detail(r)))
        check("the manager's draw is written", len(matches_of(h, tid_b)) == 6,
              "matches=%d" % len(matches_of(h, tid_b)))

    # Ownership switched off (a single-operator instance): every admin is a
    # manager, and a player still is not. Built last, because the switch is
    # process-wide and the next Harness() puts it back.
    h2 = Harness(enforce_ownership=False)
    owner2 = h2.make_user("Owner", "admin")
    other2 = h2.make_user("Other Admin", "admin")
    player2 = h2.make_user("A Player", "player")
    tid2 = create_tournament(h2, owner2, "round_robin", boards=2)
    if tid2 and approve_pool(h2, owner2, tid2, 4):
        r = h2.post("/api/fixtures/%s/generate" % tid2, user_id=other2)
        check("with ownership unenforced any admin may draw", r.status_code == 200,
              "%s %s" % (r.status_code, detail(r)))
        r = h2.post("/api/fixtures/%s/generate" % tid2, user_id=player2)
        check("with ownership unenforced a player still may not draw",
              r.status_code == 403, "%s %s" % (r.status_code, detail(r)))




# ---------------------------------------------------------------------------
# DELETE /matches/{id}: removing one fixture from a draw
#
# A draw could be added to and never subtracted from, so the only way to be rid
# of a fixture that should not be played was to regenerate -- which deletes
# every result in the tournament. These cases pin what one deletion may and may
# not take with it.
# ---------------------------------------------------------------------------

def score_one_board(h, admin, match_id):
    """Put play on a match the way an umpire does, through the API."""
    h.post("/api/matches/%s/start" % match_id, {}, user_id=admin)
    return h.post("/api/matches/%s/boards/1/submit" % match_id, dict(SCORE), user_id=admin)


def test_unplayed_fixture_deletes_cleanly():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    check("the draw is generated", h.post("/api/tournaments/%s/fixtures" % tid, {},
                                          user_id=admin).status_code == 200)

    before = matches_of(h, tid)
    victim = before[0]
    boards_before = len([b for b in h.db.rows("boards") if b["match_id"] == victim["id"]])
    check("the fixture has boards to lose", boards_before > 0, boards_before)

    r = h.delete("/api/matches/%s" % victim["id"], user_id=admin)
    if not check("an unplayed fixture can be removed", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return
    payload = body(r)

    check("the removal names the match it removed",
          payload.get("matchId") == victim["id"], payload)
    check("the removal reports the boards that went with it",
          payload.get("boardsDeleted") == boards_before, payload)
    check("an unplayed removal does not report discarded play",
          payload.get("discardedPlay") is False, payload)
    check("the fixture is gone from the draw",
          len(matches_of(h, tid)) == len(before) - 1, len(matches_of(h, tid)))
    check("no other fixture is removed with it",
          {m["id"] for m in matches_of(h, tid)} == {m["id"] for m in before} - {victim["id"]})
    check("the boards go with the fixture",
          not [b for b in h.db.rows("boards") if b["match_id"] == victim["id"]],
          [b for b in h.db.rows("boards") if b["match_id"] == victim["id"]])

    # The pair now has no fixture, so the table must show them a match lighter.
    standings = body(h.get("/api/standings/%s" % tid, admin))
    rows = (standings.get("categories") or [{}])[0].get("standings") or []
    check("the standings still compute after a fixture is removed", bool(rows), standings)


def test_played_fixture_is_protected():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)

    victim = matches_of(h, tid)[0]
    if not check("a board can be scored on the fixture",
                 score_one_board(h, admin, victim["id"]).status_code == 200):
        return

    r = h.delete("/api/matches/%s" % victim["id"], user_id=admin)
    check("a fixture with play on it is not deleted by accident", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))
    check("the refusal says what would be lost",
          "board" in detail(r).lower() and "correction history" in detail(r).lower(),
          detail(r))
    check("the refused fixture is still there",
          any(m["id"] == victim["id"] for m in matches_of(h, tid)))
    check("the refused fixture keeps its boards",
          bool([b for b in h.db.rows("boards") if b["match_id"] == victim["id"]]))

    # force is the organiser saying they accept the loss.
    r = h.delete("/api/matches/%s?force=true" % victim["id"], user_id=admin)
    if not check("force deletes a played fixture", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return
    check("a forced removal reports that play was discarded",
          body(r).get("discardedPlay") is True, body(r))
    check("the forced removal is gone",
          not any(m["id"] == victim["id"] for m in matches_of(h, tid)))
    check("its boards go with it",
          not [b for b in h.db.rows("boards") if b["match_id"] == victim["id"]])


def test_confirmed_result_leaves_the_points_table():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)

    victim = matches_of(h, tid)[0]
    # A walkover is the cheapest confirmed result to produce.
    h.post("/api/matches/%s/walkover" % victim["id"],
           {"winnerId": victim["player1_id"], "reason": "no show"}, user_id=admin)
    h.post("/api/matches/%s/confirm" % victim["id"], {}, user_id=admin)

    def played_of(pid):
        standings = body(h.get("/api/standings/%s" % tid, admin))
        rows = (standings.get("categories") or [{}])[0].get("standings") or []
        row = next((x for x in rows if x.get("participantId") == pid), {})
        return row.get("played"), row.get("points")

    before = played_of(victim["player1_id"])
    check("the confirmed result is in the table", before[0] == 1, before)

    r = h.delete("/api/matches/%s" % victim["id"], user_id=admin)
    check("a confirmed result is not deleted without force", r.status_code == 409, detail(r))
    check("the confirmed result is still counted", played_of(victim["player1_id"]) == before)

    r = h.delete("/api/matches/%s?force=true" % victim["id"], user_id=admin)
    if not check("a confirmed result can be deleted with force", r.status_code == 200,
                 detail(r)):
        return
    after = played_of(victim["player1_id"])
    check("the deleted result leaves the points table", after == (0, 0), after)


def test_deleting_a_bracket_match_keeps_the_bracket_honest():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "knockout", boards=2)
    if not tid or not approve_pool(h, admin, tid, 8):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)

    ko = [m for m in matches_of(h, tid) if m.get("stage") == "knockout"]
    fed = {m.get("next_match_id") for m in ko if m.get("next_match_id")}
    first = [m for m in ko if m["id"] not in fed]
    later = [m for m in ko if m["id"] in fed]
    if not check("the knockout has a round that others feed", bool(later), len(ko)):
        return

    # A later-round match is where its feeders send their winners. Deleting it
    # would leave them pointing at nothing -- silently, because the constraint
    # is ON DELETE SET NULL.
    target = later[0]
    feeders = [m for m in ko if m.get("next_match_id") == target["id"]]
    r = h.delete("/api/matches/%s" % target["id"], user_id=admin)
    check("a match other matches advance into is not deleted by accident",
          r.status_code == 409, "%s %s" % (r.status_code, detail(r)))
    check("the refusal says how many would be orphaned",
          str(len(feeders)) in detail(r), detail(r))
    check("the refused bracket match survives",
          any(m["id"] == target["id"] for m in matches_of(h, tid)))
    check("its feeders keep their links",
          all(m.get("next_match_id") == target["id"]
              for m in matches_of(h, tid) if m["id"] in {f["id"] for f in feeders}),
          [m.get("next_match_id") for m in matches_of(h, tid)
           if m["id"] in {f["id"] for f in feeders}])

    # Forced, it goes -- and the feeders are reported, not quietly broken.
    r = h.delete("/api/matches/%s?force=true" % target["id"], user_id=admin)
    if not check("force deletes a match others feed", r.status_code == 200, detail(r)):
        return
    check("the orphaned feeders are named back to the caller",
          sorted(f["matchNumber"] for f in body(r).get("orphanedFeeders") or [])
          == sorted(f["match_number"] for f in feeders), body(r).get("orphanedFeeders"))
    check("the feeders survive the deletion of the round above them",
          all(any(m["id"] == f["id"] for m in matches_of(h, tid)) for f in feeders))
    check("the feeders' links are cleared rather than left dangling",
          all(m.get("next_match_id") is None for m in matches_of(h, tid)
              if m["id"] in {f["id"] for f in feeders}),
          [m.get("next_match_id") for m in matches_of(h, tid)
           if m["id"] in {f["id"] for f in feeders}])

    # A first-round match that has already sent its winner up: deleting it must
    # put that slot back to waiting, not leave a finalist with no semi-final.
    h2 = Harness()
    admin2 = h2.make_user("Owner", "admin")
    tid2 = create_tournament(h2, admin2, "knockout", boards=2)
    if not tid2 or not approve_pool(h2, admin2, tid2, 8):
        return
    h2.post("/api/tournaments/%s/fixtures" % tid2, {}, user_id=admin2)
    ko2 = [m for m in matches_of(h2, tid2) if m.get("stage") == "knockout"]
    fed2 = {m.get("next_match_id") for m in ko2 if m.get("next_match_id")}
    qf = next(m for m in ko2 if m["id"] not in fed2 and m.get("next_match_id"))

    h2.post("/api/matches/%s/walkover" % qf["id"],
            {"winnerId": qf["player1_id"], "reason": "no show"}, user_id=admin2)
    h2.post("/api/matches/%s/confirm" % qf["id"], {}, user_id=admin2)
    parent = next(m for m in matches_of(h2, tid2) if m["id"] == qf["next_match_id"])
    slot = qf["next_match_slot"]
    if not check("the winner was promoted into the next round",
                 parent.get("%s_id" % slot) == qf["player1_id"],
                 "%s -> %s" % (slot, parent.get("%s_name" % slot))):
        return

    r = h2.delete("/api/matches/%s?force=true" % qf["id"], user_id=admin2)
    if not check("a promoted fixture can be removed with force", r.status_code == 200,
                 detail(r)):
        return
    parent = next(m for m in matches_of(h2, tid2) if m["id"] == qf["next_match_id"])
    check("the slot it had filled goes back to waiting",
          parent.get("%s_id" % slot) is None
          and parent.get("%s_name" % slot) == "Winner TBD",
          "%s = %s / %s" % (slot, parent.get("%s_id" % slot), parent.get("%s_name" % slot)))
    check("the caller is told which slot was cleared",
          (body(r).get("clearedSlot") or {}).get("slot") == slot, body(r).get("clearedSlot"))


def test_removal_is_owner_only():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    other = h.make_user("Other Admin", "admin")
    player = h.make_user("Player One")
    tid = create_tournament(h, owner, "round_robin", boards=2)
    if not tid or not approve_pool(h, owner, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=owner)
    victim = matches_of(h, tid)[0]

    r = h.delete("/api/matches/%s" % victim["id"], user_id=player)
    check("a player cannot remove a fixture", r.status_code in (401, 403),
          "%s %s" % (r.status_code, detail(r)))
    r = h.delete("/api/matches/%s" % victim["id"], user_id=other)
    check("an admin who does not run the tournament cannot remove a fixture",
          r.status_code == 403, "%s %s" % (r.status_code, detail(r)))
    r = h.delete("/api/matches/%s" % victim["id"])
    check("an anonymous caller cannot remove a fixture", r.status_code in (401, 403),
          "%s %s" % (r.status_code, detail(r)))
    check("the fixture survives every refused attempt",
          any(m["id"] == victim["id"] for m in matches_of(h, tid)))

    r = h.delete("/api/matches/%s" % str(uuid.uuid4()), user_id=owner)
    check("removing a match that does not exist is a 404", r.status_code == 404,
          "%s %s" % (r.status_code, detail(r)))




def test_an_untouched_draw_redraws_without_force():
    """
    A draw nobody has played is not a draw with results on it.

    Every match is generated with its first board 'in_progress' -- that is how
    the board is queued for the umpire -- and the guard counted anything not
    pending as play. So a draw made thirty seconds ago, with nothing scored on
    it, was refused with "6 board(s) with play recorded on them" and could only
    be redrawn by confirming the discard of results that did not exist. The
    warning has to mean something the first time an organiser sees it.
    """
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    if not check("the first draw succeeds",
                 h.post("/api/tournaments/%s/fixtures" % tid, {},
                        user_id=admin).status_code == 200):
        return

    boards = [b for b in h.db.rows("boards")]
    queued = [b for b in boards if b.get("status") == "in_progress"]
    check("a fresh draw really does queue a board per match",
          len(queued) == len(matches_of(h, tid)), "%d queued" % len(queued))
    check("but none of them is scored",
          not [b for b in boards if b.get("status") == "completed"
               or (b.get("player1_score") or 0) or (b.get("player2_score") or 0)])

    before = match_ids(h, tid)
    r = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    if not check("an untouched draw can be redrawn without force",
                 r.status_code == 200, "%s %s" % (r.status_code, detail(r))):
        return
    check("redrawing replaces the draw", match_ids(h, tid) != before,
          "same ids after redraw")
    check("the redraw produces a full draw again",
          len(matches_of(h, tid)) == len(before), len(matches_of(h, tid)))

    # And the guard still fires the moment there is something to lose.
    victim = matches_of(h, tid)[0]
    h.post("/api/matches/%s/start" % victim["id"], {}, user_id=admin)
    h.post("/api/matches/%s/boards/1/submit" % victim["id"], dict(SCORE), user_id=admin)
    r = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    check("a draw with one board scored still refuses to be redrawn",
          r.status_code == 409, "%s %s" % (r.status_code, detail(r)))
    check("and the refusal counts only the board that was played",
          "1 board(s) with play recorded" in detail(r), detail(r))




# ---------------------------------------------------------------------------
# PUT /matches/{id}: editing a fixture and its schedule
#
# The draw could be added to and removed from but never corrected, so a
# mis-entered pairing had to be deleted and made again -- losing its match
# number and any boards scored on it. These cases pin what an edit may change
# and what it must refuse to touch.
# ---------------------------------------------------------------------------

def entrant_ids(h, tid):
    """The approved participants, in a stable order."""
    return sorted(r["player_id"] for r in h.db.rows("registrations")
                  if r["tournament_id"] == tid and r.get("status") == "approved")


def test_rescheduling_a_fixture():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=3)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    m = matches_of(h, tid)[0]

    r = h.put("/api/matches/%s" % m["id"],
              {"boardNumber": 3, "scheduledDate": "2026-04-02",
               "scheduledTime": "4:30 PM", "roundName": "Rescheduled Round"},
              user_id=admin)
    if not check("a fixture can be rescheduled", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return

    payload = body(r)
    check("the edit reports exactly what it changed",
          payload.get("changed") == sorted(
              ["board_number", "round_name", "scheduled_date", "scheduled_time"]),
          payload.get("changed"))

    after = next(x for x in matches_of(h, tid) if x["id"] == m["id"])
    check("the board is moved", after["board_number"] == 3, after["board_number"])
    check("the date is moved", after["scheduled_date"] == "2026-04-02", after["scheduled_date"])
    check("the time is moved", after["scheduled_time"] == "4:30 PM", after["scheduled_time"])
    check("the round is renamed", after["round_name"] == "Rescheduled Round", after["round_name"])
    check("rescheduling does not touch the pairing",
          (after["player1_id"], after["player2_id"]) == (m["player1_id"], m["player2_id"]))
    check("rescheduling does not touch the match number",
          after["match_number"] == m["match_number"])

    # Only what is sent is written.
    r = h.put("/api/matches/%s" % m["id"], {"boardNumber": 1}, user_id=admin)
    check("a partial edit writes only that field",
          r.status_code == 200 and body(r).get("changed") == ["board_number"], body(r))
    after = next(x for x in matches_of(h, tid) if x["id"] == m["id"])
    check("the fields not sent are left alone",
          after["scheduled_time"] == "4:30 PM" and after["round_name"] == "Rescheduled Round",
          (after["scheduled_time"], after["round_name"]))

    r = h.put("/api/matches/%s" % m["id"], {}, user_id=admin)
    check("an empty edit is refused rather than silently doing nothing",
          r.status_code == 422, "%s %s" % (r.status_code, detail(r)))


def test_repairing_a_fixture():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    m = matches_of(h, tid)[0]
    ids = entrant_ids(h, tid)
    spare = next(p for p in ids if p not in (m["player1_id"], m["player2_id"]))

    r = h.put("/api/matches/%s" % m["id"], {"player2Id": spare}, user_id=admin)
    if not check("an unplayed fixture can be re-paired", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return
    after = next(x for x in matches_of(h, tid) if x["id"] == m["id"])
    check("the new player is written", after["player2_id"] == spare, after["player2_id"])
    check("the name follows the id",
          after["player2_name"] and after["player2_name"] != m["player2_name"],
          after["player2_name"])
    check("the other side is untouched", after["player1_id"] == m["player1_id"])

    # The guards on who may be fixtured.
    r = h.put("/api/matches/%s" % m["id"], {"player2Id": after["player1_id"]}, user_id=admin)
    check("a player cannot be fixtured against themselves", r.status_code == 422,
          "%s %s" % (r.status_code, detail(r)))

    outsider = h.make_user("Not Entered")
    r = h.put("/api/matches/%s" % m["id"], {"player1Id": outsider}, user_id=admin)
    check("somebody who never entered cannot be fixtured", r.status_code == 422,
          "%s %s" % (r.status_code, detail(r)))
    after = next(x for x in matches_of(h, tid) if x["id"] == m["id"])
    check("a refused edit changes nothing", after["player1_id"] == m["player1_id"])

    r = h.put("/api/matches/%s" % m["id"], {"stage": "semi final"}, user_id=admin)
    check("an unknown stage is refused", r.status_code == 422,
          "%s %s" % (r.status_code, detail(r)))


def test_a_played_fixture_cannot_be_quietly_repaired():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    m = matches_of(h, tid)[0]
    ids = entrant_ids(h, tid)
    spare = next(p for p in ids if p not in (m["player1_id"], m["player2_id"]))

    h.post("/api/matches/%s/start" % m["id"], {}, user_id=admin)
    if not check("a board can be scored on it",
                 h.post("/api/matches/%s/boards/1/submit" % m["id"], dict(SCORE),
                        user_id=admin).status_code == 200):
        return

    r = h.put("/api/matches/%s" % m["id"], {"player2Id": spare}, user_id=admin)
    check("re-pairing a played fixture is refused", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))
    check("the refusal explains the scores would move with it",
          "points table" in detail(r).lower(), detail(r))
    after = next(x for x in matches_of(h, tid) if x["id"] == m["id"])
    check("the played fixture keeps its pairing", after["player2_id"] == m["player2_id"])

    # Rescheduling it is still fine -- moving a board is not re-pairing.
    r = h.put("/api/matches/%s" % m["id"], {"boardNumber": 2}, user_id=admin)
    check("a played fixture can still be moved to another board",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))

    r = h.put("/api/matches/%s?force=true" % m["id"],
              {"player2Id": spare, "reason": "wrong pair entered"}, user_id=admin)
    if not check("force re-pairs a played fixture", r.status_code == 200, detail(r)):
        return
    check("the caller is warned the boards stay with it",
          any("board" in w for w in body(r).get("warnings") or []), body(r).get("warnings"))
    after = next(x for x in matches_of(h, tid) if x["id"] == m["id"])
    check("the forced re-pairing is written", after["player2_id"] == spare)


def test_the_result_cannot_be_edited_through_the_fixture_route():
    """
    The edit route takes the fixture and the schedule, never the outcome.

    models/match.py also defines MatchUpdateSchema, which carries winner_id,
    result_confirmed and the board-win totals. It is unused, and wiring it to
    this route would let a caller write a result without playing it. Pydantic
    ignores unknown keys, so these are silently dropped rather than refused --
    what matters is that they never reach the row.
    """
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    m = matches_of(h, tid)[0]

    r = h.put("/api/matches/%s" % m["id"], {
        "boardNumber": 2,
        "winnerId": m["player1_id"], "winnerName": "Cheat",
        "resultConfirmed": True, "status": "completed",
        "player1BoardWins": 9, "player1TotalPoints": 99,
    }, user_id=admin)
    if not check("an edit carrying result fields is still accepted for its real fields",
                 r.status_code == 200, "%s %s" % (r.status_code, detail(r))):
        return
    check("only the fixture field is reported as changed",
          body(r).get("changed") == ["board_number"], body(r).get("changed"))

    after = next(x for x in matches_of(h, tid) if x["id"] == m["id"])
    check("no winner is written", not after.get("winner_id"), after.get("winner_id"))
    check("the result is not confirmed", not after.get("result_confirmed"))
    check("the status is not forced to completed",
          after.get("status") != "completed", after.get("status"))
    # Compared against what the row held before, not against 0: the real
    # column defaults to 0 NOT NULL, the in-memory database applies no
    # defaults, and either way "unchanged" is the property being tested.
    check("the board wins are untouched",
          (after.get("player1_board_wins"), after.get("player1_total_points"))
          == (m.get("player1_board_wins"), m.get("player1_total_points")),
          (after.get("player1_board_wins"), after.get("player1_total_points")))
    check("the injected totals never reach the row",
          after.get("player1_board_wins") != 9 and after.get("player1_total_points") != 99,
          (after.get("player1_board_wins"), after.get("player1_total_points")))


def test_a_clash_is_reported_not_refused():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=3)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    a, b = matches_of(h, tid)[0], matches_of(h, tid)[1]

    h.put("/api/matches/%s" % a["id"],
          {"boardNumber": 2, "scheduledDate": "2026-04-03", "scheduledTime": "10:00 AM"},
          user_id=admin)
    r = h.put("/api/matches/%s" % b["id"],
              {"boardNumber": 2, "scheduledDate": "2026-04-03", "scheduledTime": "10:00 AM"},
              user_id=admin)
    if not check("a clashing move is allowed", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return
    warnings = body(r).get("warnings") or []
    check("but the double-booked board is reported",
          any("board 2" in w for w in warnings), warnings)

    after = next(x for x in matches_of(h, tid) if x["id"] == b["id"])
    check("the move still went through", after["board_number"] == 2, after["board_number"])

    # A clear slot draws no warning at all.
    r = h.put("/api/matches/%s" % b["id"],
              {"boardNumber": 3, "scheduledTime": "2:00 PM"}, user_id=admin)
    check("an uncontested slot warns about nothing",
          r.status_code == 200 and not (body(r).get("warnings") or []),
          body(r).get("warnings"))


def test_editing_is_owner_only():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    other = h.make_user("Other Admin", "admin")
    player = h.make_user("Player One")
    tid = create_tournament(h, owner, "round_robin", boards=2)
    if not tid or not approve_pool(h, owner, tid, 4):
        return
    h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=owner)
    m = matches_of(h, tid)[0]

    for who, label, expected in ((player, "a player", (401, 403)),
                                 (other, "an admin who does not run it", (403,)),
                                 (None, "an anonymous caller", (401, 403))):
        r = h.put("/api/matches/%s" % m["id"], {"boardNumber": 2}, user_id=who)
        check("%s cannot edit a fixture" % label, r.status_code in expected,
              "%s %s" % (r.status_code, detail(r)))

    after = next(x for x in matches_of(h, tid) if x["id"] == m["id"])
    check("the fixture is unchanged by every refused edit",
          after["board_number"] == m["board_number"], after["board_number"])

    r = h.put("/api/matches/%s" % str(uuid.uuid4()), {"boardNumber": 2}, user_id=owner)
    check("editing a match that does not exist is a 404", r.status_code == 404,
          "%s %s" % (r.status_code, detail(r)))




# ---------------------------------------------------------------------------
# Entries close when registration closes
#
# The single-entry route checked the tournament's state for players and waved
# organisers through at any stage; the bulk import checked nothing at all. So a
# participant could be entered into a tournament whose draw was already made
# and half played -- they appear in the entry list and nowhere else, with no
# fixtures and no place in the table.
# ---------------------------------------------------------------------------

CLOSED_STATES = ("registration_closed", "fixture_generation", "fixture_published",
                 "in_progress", "completed")


def set_state(h, tid, status):
    h.db.table("tournaments").update({"status": status}).eq("id", tid).execute()


def enter_one(h, who, tid, name, force=False):
    """Create a player and try to enter them, as the organiser's desk does."""
    rp = h.post("/api/players", {"name": name,
                                 "email": "%s@carrom.example.com" % name.replace(" ", "").lower(),
                                 "rating": 1500}, user_id=who)
    if rp.status_code != 200:
        return rp
    path = "/api/tournaments/%s/registrations" % tid
    if force:
        path += "?force=true"
    return h.post(path, {"type": "singles", "playerId": body(rp).get("id")}, user_id=who)


def registration_count(h, tid):
    return len([r for r in h.db.rows("registrations") if r["tournament_id"] == tid])


def test_entries_close_with_registration():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return

    # While the desk is open, an organiser enters people freely.
    set_state(h, tid, "registration_open")
    before = registration_count(h, tid)
    r = enter_one(h, admin, tid, "Late Alice")
    check("an organiser can enter a participant while registration is open",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))
    check("the entry is written", registration_count(h, tid) == before + 1,
          registration_count(h, tid))

    # A draft tournament has not opened yet; its list is still being built.
    set_state(h, tid, "draft")
    r = enter_one(h, admin, tid, "Draft Bob")
    check("a draft tournament still accepts entries", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    # Every state after the desk closes refuses.
    for status in CLOSED_STATES:
        set_state(h, tid, status)
        held = registration_count(h, tid)
        r = enter_one(h, admin, tid, "Toolate %s" % status)
        check("an organiser cannot enter a participant once registration is closed",
              r.status_code == 409, "%s -> %s %s" % (status, r.status_code, detail(r)))
        check("the refusal names the state it is in",
              status.replace("_", " ") in detail(r) or status in detail(r),
              "%s: %s" % (status, detail(r)))
        check("nobody is entered by a refused attempt",
              registration_count(h, tid) == held,
              "%s: %d -> %d" % (status, held, registration_count(h, tid)))

    # A player's own entry was already refused, and still is.
    set_state(h, tid, "registration_closed")
    player = h.make_user("Hopeful Player")
    r = h.post("/api/tournaments/%s/registrations" % tid,
               {"type": "singles", "playerId": player}, user_id=player)
    check("a player cannot enter a closed tournament either", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))


def test_the_organiser_keeps_a_deliberate_way_in():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid or not approve_pool(h, admin, tid, 4):
        return
    set_state(h, tid, "registration_closed")

    before = registration_count(h, tid)
    r = enter_one(h, admin, tid, "Forced Carol", force=True)
    check("force enters a participant after the desk has closed",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))
    check("the forced entry is written", registration_count(h, tid) == before + 1,
          registration_count(h, tid))

    # Reopening is the other way, and needs no override at all.
    set_state(h, tid, "registration_open")
    r = enter_one(h, admin, tid, "Reopened Dave")
    check("reopening registration restores ordinary entry", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))


def test_bulk_import_closes_with_it():
    h = Harness()
    admin = h.make_user("Owner", "admin")
    tid = create_tournament(h, admin, "round_robin", boards=2)
    if not tid:
        return

    sheet = [{"name": "Sheet One", "email": "sheet1@carrom.example.com"},
             {"name": "Sheet Two", "email": "sheet2@carrom.example.com"}]

    set_state(h, tid, "registration_open")
    r = confirm_import(h, admin, tid, sheet)
    check("a sheet imports while registration is open", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    opened = registration_count(h, tid)
    check("the sheet's entries are written", opened >= 2, opened)

    # The door the single-entry guard left standing open.
    set_state(h, tid, "in_progress")
    held = registration_count(h, tid)
    r = confirm_import(h, admin, tid, [
        {"name": "Sheet Three", "email": "sheet3@carrom.example.com"}])
    check("a sheet cannot be imported into a tournament being played",
          r.status_code == 409, "%s %s" % (r.status_code, detail(r)))
    check("no row of a refused import is written",
          registration_count(h, tid) == held,
          "%d -> %d" % (held, registration_count(h, tid)))


SUITES = [
    ("draw, replay and redraw through /fixtures", test_generate_draws_replays_and_redraws),
    ("import with autoGenerate", test_import_confirm_autogenerate),
    ("access boundaries", test_access_boundaries),
    ("remove an unplayed fixture", test_unplayed_fixture_deletes_cleanly),
    ("removal guards played fixtures", test_played_fixture_is_protected),
    ("removal leaves the points table", test_confirmed_result_leaves_the_points_table),
    ("removal keeps the bracket honest", test_deleting_a_bracket_match_keeps_the_bracket_honest),
    ("removal is owner only", test_removal_is_owner_only),
    ("untouched draw redraws freely", test_an_untouched_draw_redraws_without_force),
    ("reschedule a fixture", test_rescheduling_a_fixture),
    ("re-pair a fixture", test_repairing_a_fixture),
    ("played fixtures resist re-pairing", test_a_played_fixture_cannot_be_quietly_repaired),
    ("the result is not editable here", test_the_result_cannot_be_edited_through_the_fixture_route),
    ("clashes are reported", test_a_clash_is_reported_not_refused),
    ("editing is owner only", test_editing_is_owner_only),
    ("entries close with registration", test_entries_close_with_registration),
    ("the organiser keeps a way in", test_the_organiser_keeps_a_deliberate_way_in),
    ("bulk import closes too", test_bulk_import_closes_with_it),
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
    print("fixture routes (/fixtures/{id}/generate and /imports/confirm autoGenerate)")
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
