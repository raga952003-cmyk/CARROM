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


SUITES = [
    ("draw, replay and redraw through /fixtures", test_generate_draws_replays_and_redraws),
    ("import with autoGenerate", test_import_confirm_autogenerate),
    ("access boundaries", test_access_boundaries),
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
