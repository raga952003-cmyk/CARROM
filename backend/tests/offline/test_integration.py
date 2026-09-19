"""
Integration tests: routers + dependencies + services + serializers, over HTTP,
against the in-memory database.

These are the layer the existing suites could not cover safely. backend/tests/*
drives a live API and writes real rows; this drives the SAME application through
Starlette's TestClient with Supabase replaced by fakedb, so it can run during an
event, in CI, on a laptop with no network.

Technique: mostly WHITE BOX. Each case is aimed at a specific branch that was
identified by reading the code -- the profile-lookup fallback, each arm of
describe_access, the idempotency replay path, the degraded-migration paths.
Black-box cases (equivalence classes, boundaries) live in test_system.py and
test_acceptance.py.

NOT COVERED HERE: row-level security. Policies live in Postgres and fakedb does
not evaluate them, so any endpoint relying on RLS alone -- notably
PUT /notifications/{id}/read, which filters only on id -- looks safe here and is
not actually checked. That gap belongs to the live suites.

    python tests/offline/test_integration.py
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


# ---------------------------------------------------------------------------
# security.get_user_profile -- every branch of the identity lookup
# ---------------------------------------------------------------------------

def test_identity_branches():
    h = Harness()

    # Branch 1: the profiles row exists. The row is the authority.
    admin = h.make_user("Owner", "admin")
    h.db.tables["profiles"][0]["name"] = "From The Table"
    r = h.get("/api/auth/me", admin)
    check("a signed-in user with a profile row reads their profile",
          r.status_code == 200 and body(r).get("name") == "From The Table",
          "%s %s" % (r.status_code, body(r)))

    # Branch 2: no profiles row. The API answers from token claims instead.
    ghost = h.make_user("Ghost Admin", "admin", with_profile=False)
    r = h.get("/api/auth/me", ghost)
    check("a user with no profile row is still served a profile",
          r.status_code == 200, "%s %s" % (r.status_code, body(r)))
    check("the fabricated profile carries the role from token metadata",
          body(r).get("role") == "admin", body(r))

    # Branch 3: no credentials at all.
    r = h.client.get("/api/auth/me")
    check("an unauthenticated request is refused with 401", r.status_code == 401,
          "%s %s" % (r.status_code, body(r)))

    # Branch 4: a token for a user that does not exist.
    r = h.client.get("/api/auth/me", headers={"Authorization": "Bearer tok:nobody"})
    check("a token for an unknown user is refused with 401", r.status_code == 401,
          "%s %s" % (r.status_code, body(r)))


# ---------------------------------------------------------------------------
# The failure that started this: a profile-less identity reaching an FK write
# ---------------------------------------------------------------------------

def test_profileless_admin_scoring():
    h = Harness()
    ghost = h.make_user("Ghost Admin", "admin", with_profile=False)
    tid = h.seed_tournament(owner_id=None)
    p1 = h.make_user("Player One")
    p2 = h.make_user("Player Two")
    mid = h.seed_match(tid, p1, p2, boards=8)

    r = h.post("/api/matches/%s/boards/1/submit" % mid, {
        "p1Score": 0, "p2Score": 0, "setNumber": 1,
        "boardWinner": "player1", "coinsRemainingWith": "player2",
        "coinsRemaining": 5, "queenPocketedBy": "player1",
        "queenCoveredBy": "player1",
    }, user_id=ghost)

    text = detail(r)
    check("scoring a board never returns a raw database error to the caller",
          "23503" not in text and "violates foreign key" not in text,
          "%s %s" % (r.status_code, text))
    check("scoring a board never surfaces an internal constraint name",
          "_fkey" not in text, "%s %s" % (r.status_code, text))
    check("an identity the database will reject cannot score a board",
          r.status_code != 500, "%s %s" % (r.status_code, text))
    check("either the board is recorded, or the refusal explains itself",
          r.status_code < 400 or (len(text) > 20 and "{" not in text),
          "%s %s" % (r.status_code, text))

    # A properly provisioned admin must still be able to score.
    good = h.make_user("Real Admin", "admin")
    r2 = h.post("/api/matches/%s/boards/2/submit" % mid, {
        "p1Score": 0, "p2Score": 0, "setNumber": 1,
        "boardWinner": "player1", "coinsRemainingWith": "player2",
        "coinsRemaining": 5, "queenPocketedBy": "none",
        "queenCoveredBy": "none",
    }, user_id=good)
    check("an admin with a profile row can score a board",
          r2.status_code == 200, "%s %s" % (r2.status_code, detail(r2)))


# ---------------------------------------------------------------------------
# The orphan factory: creating a player when the trigger is not installed
# ---------------------------------------------------------------------------

def test_player_creation_without_trigger():
    h = Harness(trigger_enabled=False)
    admin = h.make_user("Admin", "admin")

    before_auth = len(h.db.auth_users)
    r = h.post("/api/players", {"name": "New Player", "email": "new@carrom.example.com",
                                "rating": 1500}, user_id=admin)
    after_auth = len(h.db.auth_users)
    profiles = [p["name"] for p in h.db.rows("profiles")]

    check("creating a player either succeeds or leaves nothing behind",
          r.status_code < 400 or after_auth == before_auth,
          "status=%s auth_users %d -> %d profiles=%s"
          % (r.status_code, before_auth, after_auth, profiles))
    check("a player that was created has a profile row",
          r.status_code >= 400 or "New Player" in profiles,
          "status=%s profiles=%s" % (r.status_code, profiles))
    check("a failed player creation does not report a raw Python error",
          r.status_code < 400 or "index out of range" not in detail(r),
          detail(r))

    # With the trigger present the same call must work.
    h2 = Harness(trigger_enabled=True)
    admin2 = h2.make_user("Admin", "admin")
    r2 = h2.post("/api/players", {"name": "New Player", "email": "n2@carrom.example.com",
                                  "rating": 1500}, user_id=admin2)
    check("creating a player works when the trigger is installed",
          r2.status_code == 200, "%s %s" % (r2.status_code, detail(r2)))


# ---------------------------------------------------------------------------
# access_control.describe_access -- one case per arm
# ---------------------------------------------------------------------------

def test_access_control_matrix():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    other = h.make_user("Other Admin", "admin")
    player = h.make_user("A Player", "player")
    tid = h.seed_tournament(owner_id=owner)
    p1, p2 = h.make_user("P One"), h.make_user("P Two")
    mid = h.seed_match(tid, p1, p2)

    payload = {"p1Score": 0, "p2Score": 0, "setNumber": 1,
               "boardWinner": "player1", "coinsRemainingWith": "player2",
               "coinsRemaining": 3, "queenPocketedBy": "none",
               "queenCoveredBy": "none"}

    r = h.post("/api/matches/%s/boards/1/submit" % mid, payload, user_id=owner)
    check("the owner can score their own tournament", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    r = h.post("/api/matches/%s/boards/2/submit" % mid, payload, user_id=other)
    check("another admin cannot score a tournament they do not own",
          r.status_code == 403, "%s %s" % (r.status_code, detail(r)))
    check("the refusal names the owner so access can be requested",
          "Owner" in detail(r) or "owner" in detail(r).lower(), detail(r))

    r = h.post("/api/matches/%s/boards/2/submit" % mid, payload, user_id=player)
    check("a player cannot score at all", r.status_code == 403,
          "%s %s" % (r.status_code, detail(r)))

    # Granted scorer: may score, may not re-draw.
    h.db.seed("tournament_access", [{
        "id": "acc-1", "tournament_id": tid, "user_id": other,
        "access_role": "scorer", "status": "approved", "decided_by": owner,
    }])
    r = h.post("/api/matches/%s/boards/2/submit" % mid, payload, user_id=other)
    check("an approved scorer can score", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    r = h.delete("/api/tournaments/%s" % tid, user_id=other)
    check("an approved scorer cannot delete the tournament",
          r.status_code == 403, "%s %s" % (r.status_code, detail(r)))

    # Revoked: back to refused, with a reason that says so.
    h.db.tables["tournament_access"][0]["status"] = "revoked"
    r = h.post("/api/matches/%s/boards/3/submit" % mid, payload, user_id=other)
    check("revoked access stops working immediately", r.status_code == 403,
          "%s %s" % (r.status_code, detail(r)))
    check("a revoked user is told their access was revoked",
          "revoke" in detail(r).lower(), detail(r))


# ---------------------------------------------------------------------------
# Authorisation boundaries on every mutating router
# ---------------------------------------------------------------------------

def test_admin_only_routes():
    h = Harness()
    player = h.make_user("Just A Player", "player")
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin)

    for method, path, payload in (
        ("post", "/api/players", {"name": "X", "rating": 1500}),
        ("post", "/api/tournaments", {"name": "X", "format": "knockout",
                                      "type": "singles"}),
        ("post", "/api/notifications", {"title": "T", "message": "M",
                                        "type": "info"}),
        ("post", "/api/ai/parse-participants", {"text": "a"}),
        ("delete", "/api/tournaments/%s" % tid, None),
    ):
        call = getattr(h, method)
        r = call(path, payload, user_id=player) if payload is not None else call(path, user_id=player)
        check("a player is refused on every admin-only route",
              r.status_code in (401, 403),
              "%s %s -> %s %s" % (method, path, r.status_code, detail(r)))

        r = call(path, payload) if payload is not None else call(path)
        check("an anonymous caller is refused on every admin-only route",
              r.status_code in (401, 403),
              "%s %s -> %s %s" % (method, path, r.status_code, detail(r)))


# ---------------------------------------------------------------------------
# Privacy: the players directory
# ---------------------------------------------------------------------------

def test_player_directory_privacy():
    h = Harness()
    admin = h.make_user("Admin", "admin")
    h.make_user("Visible Player", "player")
    h.db.tables["profiles"][-1]["phone"] = "9000000000"

    r = h.client.get("/api/players")
    check("the player directory is readable without signing in",
          r.status_code == 200, "%s %s" % (r.status_code, body(r)))
    blob = str(body(r))
    check("an anonymous reader is not given phone numbers",
          "9000000000" not in blob, blob[:200])
    check("an anonymous reader is not given email addresses",
          "@carrom.example.com" not in blob, blob[:200])

    r = h.get("/api/players", admin)
    check("an admin can still see contact details",
          r.status_code == 200, "%s" % r.status_code)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_idempotent_submission():
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin)
    p1, p2 = h.make_user("P One"), h.make_user("P Two")
    mid = h.seed_match(tid, p1, p2)

    payload = {"p1Score": 0, "p2Score": 0, "setNumber": 1,
               "boardWinner": "player1", "coinsRemainingWith": "player2",
               "coinsRemaining": 4, "queenPocketedBy": "none",
               "queenCoveredBy": "none"}
    headers = dict(h.auth(admin))
    headers["Idempotency-Key"] = "key-abc"

    first = h.client.post("/api/matches/%s/boards/1/submit" % mid,
                          json=payload, headers=headers)
    second = h.client.post("/api/matches/%s/boards/1/submit" % mid,
                           json=payload, headers=headers)
    check("a retried submission is accepted rather than rejected as a duplicate",
          second.status_code == first.status_code,
          "first=%s second=%s %s" % (first.status_code, second.status_code,
                                     detail(second)))
    if first.status_code == 200:
        check("a retried submission replays the first answer",
              body(second) == body(first),
              "first=%s second=%s" % (str(body(first))[:120],
                                      str(body(second))[:120]))
        completed = [b for b in h.db.rows("boards")
                     if b.get("status") == "completed"]
        check("a retried submission does not score the board twice",
              len(completed) == 1, "completed boards=%d" % len(completed))


# ---------------------------------------------------------------------------
# The out-of-range coin count, end to end through the API
# ---------------------------------------------------------------------------

def test_out_of_range_score_rejected():
    h = Harness()
    admin = h.make_user("Admin", "admin")
    tid = h.seed_tournament(owner_id=admin)
    p1, p2 = h.make_user("P One"), h.make_user("P Two")
    mid = h.seed_match(tid, p1, p2)

    for remaining, label in ((9, "the most a side can hold"),
                             (19, "a whole board of coins"),
                             (99, "an obvious slip"),
                             (500, "nonsense")):
        r = h.post("/api/matches/%s/boards/1/submit" % mid, {
            "p1Score": 0, "p2Score": 0, "setNumber": 1,
            "boardWinner": "player1", "coinsRemainingWith": "player2",
            "coinsRemaining": remaining, "queenPocketedBy": "none",
            "queenCoveredBy": "none",
        }, user_id=admin)
        if r.status_code == 200:
            board = [b for b in h.db.rows("boards") if b["board_number"] == 1][0]
            score = max(board.get("player1_score") or 0,
                        board.get("player2_score") or 0)
            check("a stored board score never exceeds what a board can be worth",
                  score <= 12, "coinsRemaining=%s (%s) stored=%s"
                               % (remaining, label, score))
        else:
            check("an out-of-range coin count is refused with a readable reason",
                  r.status_code == 422 and "{" not in detail(r),
                  "coinsRemaining=%s -> %s %s" % (remaining, r.status_code,
                                                  detail(r)))
        # reset for the next value
        for b in h.db.tables["boards"]:
            if b["board_number"] == 1:
                b["status"] = "in_progress"
                b["locked"] = False


# ---------------------------------------------------------------------------
# forgot-password takes its redirect from the request
# ---------------------------------------------------------------------------

def test_forgot_password_origin():
    h = Harness()
    h.make_user("Someone", "player")

    r = h.client.post("/api/auth/forgot-password",
                      json={"email": "someone@carrom.example.com"},
                      headers={"Origin": "https://carrom-umber-six.vercel.app"})
    check("a password reset request is accepted", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    sent = h.db.password_resets
    check("a reset link is requested from the provider", len(sent) == 1, sent)
    if sent:
        target = str((sent[0].get("options") or {}).get("redirect_to"))
        check("the reset link points back at the site that asked for it",
              "carrom-umber-six.vercel.app" in target, target)
        check("the reset link carries no fragment of its own",
              "#" not in target, target)

    # An address nobody holds must look identical from outside.
    r2 = h.client.post("/api/auth/forgot-password",
                       json={"email": "nobody@nowhere.example.com"},
                       headers={"Origin": "https://carrom-umber-six.vercel.app"})
    check("an unknown address gets the same answer as a known one",
          r2.status_code == r.status_code and body(r2) == body(r),
          "%s vs %s" % (body(r2), body(r)))


# ---------------------------------------------------------------------------
# Degraded schema: the app must say so rather than pretend
# ---------------------------------------------------------------------------

def test_health_reports_schema_state():
    h = Harness()
    r = h.client.get("/api/health")
    payload = body(r)
    check("the health probe answers", r.status_code == 200, payload)
    check("the health probe reports the environment",
          payload.get("env") == "test", payload)
    check("the health probe names its migration state",
          "migrations" in payload, payload)


# ---------------------------------------------------------------------------
# The wire contract for a multi-set match
# ---------------------------------------------------------------------------

def test_multi_set_boards_survive_the_list():
    """
    Every board of every set has to come back from GET /tournaments.

    The list response leaves the zeroes off an unplayed board, which is worth
    1.4 MB a load. It used to leave the whole board off and send a count
    instead, and the client rebuilt boards 1..count -- which cannot express a
    grid. Board numbers restart at 1 in every set, so three sets of four came
    back as one set of twelve: sets two and three had nothing left to score,
    and boards 5 to 12 of set one did not exist to submit against.

    Nothing exercised this: the harness seeds single-set matches, where
    1..count happens to be right.
    """
    h = Harness()
    admin = h.make_user("Set Organiser", "admin")
    t = h.seed_tournament(admin, name="Three Setter")
    match_id = h.seed_match(t, h.make_user("Set P1"), h.make_user("Set P2"),
                            boards=4, sets=3,
                            id="33333333-3333-3333-3333-333333333333")

    payload = body(h.get("/api/tournaments", user_id=admin))
    match = next((m for tour in payload for m in tour["matches"]
                  if m["id"] == match_id), None)
    if not check("the multi-set match is in the list", match is not None, payload):
        return

    boards = match["boards"]
    check("every board row of every set is sent", len(boards) == 12,
          "%d boards for 3 sets of 4" % len(boards))
    check("boardCount agrees with what was sent",
          match["boardCount"] == len(boards), match["boardCount"])

    slots = sorted((b.get("setNumber"), b["boardNumber"]) for b in boards)
    expected = sorted((s, n) for s in (1, 2, 3) for n in (1, 2, 3, 4))
    check("each board is identified by its set as well as its number",
          slots == expected, slots)

    # And the identity is enough to score against: the board the client would
    # address as set 2, board 1 is a board the server will accept.
    scored = h.post("/api/matches/%s/boards/1/submit" % match_id,
                    {"p1Score": 0, "p2Score": 0, "setNumber": 2,
                     "boardWinner": "player1", "coinsRemainingWith": "player2",
                     "coinsRemaining": 5, "auditReason": "test"},
                    user_id=admin)
    check("a board named by set and number can be scored",
          scored.status_code == 200, detail(scored))

    # Board 1 of set 1 must still be untouched by that. A client that forgets
    # the set is refused rather than silently writing to set 1 -- which is how
    # scoring set 2 used to overwrite a played board in set 1.
    set1_board1 = next((b for b in h.db.tables["boards"]
                        if b["match_id"] == match_id
                        and b["board_number"] == 1
                        and (b.get("set_number") or 1) == 1), None)
    check("scoring set 2 leaves set 1's board 1 alone",
          set1_board1 is not None and set1_board1.get("status") != "completed",
          set1_board1)

    setless = h.post("/api/matches/%s/boards/1/submit" % match_id,
                     {"p1Score": 0, "p2Score": 0,
                      "boardWinner": "player1", "coinsRemainingWith": "player2",
                      "coinsRemaining": 5, "auditReason": "test"},
                     user_id=admin)
    check("a board write that does not say which set is refused, not guessed",
          setless.status_code == 422 and "set" in detail(setless).lower(),
          "%s %s" % (setless.status_code, detail(setless)))

    setless_fix = h.put(
        "/api/matches/%s/boards/1?reason=correction" % match_id,
        {"boardNumber": 1, "status": "completed",
         "player1Score": 5, "player2Score": 2},
        user_id=admin)
    check("a correction that does not say which set is refused too",
          setless_fix.status_code == 422 and "set" in detail(setless_fix).lower(),
          "%s %s" % (setless_fix.status_code, detail(setless_fix)))


# ---------------------------------------------------------------------------
# Rescheduling must not undo what has been played
# ---------------------------------------------------------------------------

def test_reschedule_preserves_results():
    """
    Rescheduling moves matches; it does not un-play them.

    The schedule is offered while a tournament is in progress -- the fixture
    screen has a Reschedule button -- and it reads every match row, runs the
    engine, then writes back. It used to write back the WHOLE rows it had read,
    so anything recorded on a match in between was reverted to the snapshot:
    a confirmed result lost its score and went live again, silently.
    """
    h = Harness()
    admin = h.make_user("Schedule Organiser", "admin")
    t = h.seed_tournament(admin, name="Reschedule Open", number_of_boards=2,
                          tournament_start_date="2026-09-10")
    match_id = h.seed_match(t, h.make_user("Sched P1"), h.make_user("Sched P2"),
                            boards=1, id="44444444-4444-4444-4444-444444444444",
                            round_name="Final", round_index=1, board_number=1)

    scored = h.post("/api/matches/%s/boards/1/submit" % match_id,
                    {"p1Score": 0, "p2Score": 0, "setNumber": 1,
                     "boardWinner": "player1", "coinsRemainingWith": "player2",
                     "coinsRemaining": 5, "auditReason": "played"},
                    user_id=admin)
    if not check("the board is scored before rescheduling",
                 scored.status_code == 200, detail(scored)):
        return
    # The window is between the read at the top of the request and the write at
    # the bottom, so the result has to land while the schedule is being
    # computed. That is exactly what happens at a venue -- rescheduling 190
    # fixtures takes seconds, and somebody is scoring throughout -- and
    # standing in for the engine is the only way to be inside it.
    import app.routers.tournaments as tournaments_router
    real_engine = tournaments_router.generate_conflict_free_schedule
    during = {}

    def engine_then_a_result(*args, **kwargs):
        out = real_engine(*args, **kwargs)
        during["confirm"] = h.post("/api/matches/%s/confirm" % match_id, {},
                                   user_id=admin)
        return out

    tournaments_router.generate_conflict_free_schedule = engine_then_a_result
    try:
        r = h.post("/api/tournaments/%s/schedule?restMinutes=5" % t, {}, user_id=admin)
    finally:
        tournaments_router.generate_conflict_free_schedule = real_engine

    check("the schedule is generated", r.status_code == 200, detail(r))
    check("the result was confirmed while the schedule was being built",
          during.get("confirm") is not None and during["confirm"].status_code == 200,
          detail(during["confirm"]) if during.get("confirm") else "not attempted")

    after = h.db.tables["matches"][0]
    check("rescheduling leaves the confirmed result confirmed",
          after.get("result_confirmed") is True, after.get("result_confirmed"))
    check("rescheduling does not take the winner off the match",
          bool(after.get("winner_id")), after.get("winner_id"))
    check("rescheduling does not roll the score back",
          (after.get("player1_total_points") or 0) > 0,
          after.get("player1_total_points"))
    check("rescheduling does not put a finished match back to scheduled",
          after.get("status") != "scheduled", after.get("status"))
    check("rescheduling still assigns a time",
          bool(after.get("scheduled_date")), after.get("scheduled_date"))


# ---------------------------------------------------------------------------
# Creating a tournament and publishing it in one go
# ---------------------------------------------------------------------------

def test_create_then_publish():
    """
    The create screen's Publish button: create, then open registration.

    The verbs refuse a move that would change nothing -- a second /complete
    would announce the champion to everyone twice. So a tournament created
    ALREADY open for registration cannot then be opened, and the create screen
    was doing exactly that: it wrote the status itself and then asked the verb
    to make the same move. Every publish ended in a 409.
    """
    h = Harness()
    admin = h.make_user("Create Organiser", "admin")

    created = h.post("/api/tournaments", {
        "name": "Publish Me", "format": "knockout", "type": "singles",
        "status": "draft",
        "registrationStartDate": "2026-08-01", "registrationEndDate": "2026-08-20",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-12",
        "venue": "City Sports Arena", "city": "Chennai", "rules": {},
    }, user_id=admin)
    if not check("a tournament can be created", created.status_code in (200, 201),
                 detail(created)):
        return
    new_id = body(created)["id"]

    opened = h.post("/api/tournaments/%s/open-registration" % new_id, {}, user_id=admin)
    check("a freshly created tournament can be published",
          opened.status_code == 200, detail(opened))
    check("and it lands open for registration",
          body(opened).get("status") == "registration_open", body(opened).get("status"))

    # The other half of the rule, stated so it is not lost: asking again is
    # refused, and that refusal is the reason the create screen must not write
    # the status itself.
    again = h.post("/api/tournaments/%s/open-registration" % new_id, {}, user_id=admin)
    check("publishing an already-open tournament is refused, with a reason",
          again.status_code == 409 and "already" in detail(again).lower(),
          "%s %s" % (again.status_code, detail(again)))


# ---------------------------------------------------------------------------
# Registering, in both roles
# ---------------------------------------------------------------------------

def test_registration_roles():
    """
    What you register as is what you are afterwards.

    Registering as an administrator used to produce a PLAYER account and sign
    you straight into it without a word: the form offered the role, checked a
    key against a literal in its own bundle (which also passed when the field
    was empty), and then dropped the role before sending. The server hard-coded
    'player', so the two halves never met.

    Registration is open in this deployment -- the role comes from the form and
    the server writes it -- so this is also the test that says so out loud.
    Anyone who can reach /auth/signup can create an admin.
    """
    h = Harness()

    plain = h.client.post("/api/auth/signup", json={
        "email": "plain.player@example.com", "password": "secret123",
        "name": "Plain Player", "city": "Chennai",
    })
    check("registering without naming a role succeeds", plain.status_code == 200, detail(plain))
    check("and creates a player by default",
          body(plain).get("user", {}).get("role") == "player",
          body(plain).get("user", {}).get("role"))

    admin = h.client.post("/api/auth/signup", json={
        "email": "real.admin@example.com", "password": "secret123",
        "name": "Real Admin", "city": "Chennai", "role": "admin",
    })
    check("registering as an administrator succeeds", admin.status_code == 200, detail(admin))
    check("and creates an administrator",
          body(admin).get("user", {}).get("role") == "admin",
          body(admin).get("user", {}).get("role"))

    # The session handed back is that administrator, not a player. This is the
    # exact symptom that was reported: registering as an admin logged you in
    # as a player.
    token = body(admin).get("access_token")
    me = h.client.get("/api/auth/me", headers={"Authorization": "Bearer %s" % token})
    check("the session registration hands back is the administrator's",
          body(me).get("role") == "admin", body(me).get("role"))

    # And the profiles row agrees, which is what every later authorisation
    # reads -- a session saying admin over a row saying player would be
    # refused at every door, including the sign-in that would put it right.
    row = next((pr for pr in h.db.tables["profiles"]
                if pr.get("email") == "real.admin@example.com"), None)
    check("and the stored profile says administrator too",
          (row or {}).get("role") == "admin", row)

    again = h.client.post("/api/auth/login", json={
        "email": "real.admin@example.com", "password": "secret123",
        "role": "admin"})
    check("the account can sign in as an administrator afterwards",
          again.status_code == 200, detail(again))

    # A role that is neither is refused rather than quietly made a player.
    bogus = h.client.post("/api/auth/signup", json={
        "email": "bogus.role@example.com", "password": "secret123",
        "name": "Bogus Role", "role": "superuser",
    })
    check("a role that is not player or admin is refused",
          bogus.status_code == 422, "%s %s" % (bogus.status_code, detail(bogus)))
    check("and creates no account at all",
          h.client.post("/api/auth/login", json={
              "email": "bogus.role@example.com", "password": "secret123",
              "role": "player"}).status_code != 200,
          "an account survived a refused registration")


def test_signin_role_must_match():
    """
    Signing in names a role, and the profile has the final say.

    Both directions: a player cannot sign in through the administrator door,
    and an administrator signing in as a player is refused too rather than
    silently handed a lesser session -- which is the mismatch that made the
    original report read as "it logs me in as a player".
    """
    h = Harness()
    h.client.post("/api/auth/signup", json={
        "email": "role.player@example.com", "password": "secret123",
        "name": "Role Player"})
    h.client.post("/api/auth/signup", json={
        "email": "role.admin@example.com", "password": "secret123",
        "name": "Role Admin", "role": "admin"})

    cases = [
        ("role.player@example.com", "player", 200, "a player signs in as a player"),
        ("role.player@example.com", "admin", 403, "a player cannot sign in as an admin"),
        ("role.admin@example.com", "admin", 200, "an admin signs in as an admin"),
        ("role.admin@example.com", "player", 403, "an admin is not silently downgraded"),
    ]
    for email, role, expected, label in cases:
        r = h.client.post("/api/auth/login", json={
            "email": email, "password": "secret123", "role": role})
        check(label, r.status_code == expected,
              "%s -> %s %s" % (role, r.status_code, detail(r)))
        if expected == 403:
            # The refusal has to say which door to use, or the person is stuck
            # staring at a form that will not let them in and will not say why.
            check("%s, and says which portal to use" % label,
                  "sign in through" in detail(r).lower(), detail(r))


def test_me_follows_the_profile():
    """
    /auth/me answers from the profiles row, which is what everything else
    authorises on.

    It used to read that row on the ANON client, which RLS does not let see
    other people's profiles, so the read came back empty and the reply was
    assembled from the token's own claims. A promotion or demotion was then
    invisible for the life of the token -- the app routes a reload on this
    answer -- and the reply carried four fields instead of the profile, so the
    settings form opened blank after a reload and could save the blanks back.
    """
    h = Harness()
    h.client.post("/api/auth/signup", json={
        "email": "promote.me@example.com", "password": "secret123",
        "name": "Promote Me", "club": "Deccan Club", "city": "Chennai",
        "phone": "9876543210"})
    session = h.client.post("/api/auth/login", json={
        "email": "promote.me@example.com", "password": "secret123",
        "role": "player"})
    token = body(session).get("access_token")
    auth = {"Authorization": "Bearer %s" % token}

    me = body(h.client.get("/api/auth/me", headers=auth))
    check("who I am comes back as a player", me.get("role") == "player", me.get("role"))
    for field, expected in (("club", "Deccan Club"), ("city", "Chennai"),
                            ("phone", "9876543210")):
        check("who I am carries my %s" % field, me.get(field) == expected,
              "%s = %r" % (field, me.get(field)))

    # Now with RLS in the way, which is the case that was actually broken.
    #
    # fakedb does not evaluate policies, so the anon client here is blinded by
    # hand: in Postgres, `profiles` is not readable by the anon role, and the
    # shared client this endpoint used has no user bound to it, so the read
    # came back empty and the answer was assembled from the token instead.
    import app.database as database
    real_client = database.supabase_client

    class _AnonBlindToProfiles:
        """Everything as before, except reading profiles, which RLS refuses."""
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def table(self, name):
            if name != "profiles":
                return self._inner.table(name)

            class _Empty:
                def select(self, *a, **k): return self
                def eq(self, *a, **k): return self
                def execute(self):
                    class R: data = []
                    return R()
            return _Empty()

    database.supabase_client = _AnonBlindToProfiles(real_client)
    try:
        blinded = body(h.client.get("/api/auth/me", headers=auth))
    finally:
        database.supabase_client = real_client

    check("who I am survives RLS on the profiles table",
          blinded.get("role") == "player" and blinded.get("club") == "Deccan Club",
          "role=%r club=%r" % (blinded.get("role"), blinded.get("club")))

    # An organiser promotes them in the profiles table. The token in the
    # browser still says player, and must not be what answers.
    row = next(pr for pr in h.db.tables["profiles"]
               if pr.get("email") == "promote.me@example.com")
    row["role"] = "admin"

    after = body(h.client.get("/api/auth/me", headers=auth))
    check("a promotion is visible without signing in again",
          after.get("role") == "admin", after.get("role"))

    # And a demotion the same way round, which is the direction that matters:
    # an admin whose rights were taken away must stop being served them.
    row["role"] = "player"
    back = body(h.client.get("/api/auth/me", headers=auth))
    check("a demotion is visible without signing in again",
          back.get("role") == "player", back.get("role"))


def test_players_can_only_enter_themselves():
    """
    Entering somebody else is an organiser's job.

    player_id came straight from the request body with nothing checking it
    against the caller, so any signed-in player could enter anyone in the
    directory under their name -- and the organiser approving it had no way to
    tell it apart from a real entry. Migration 013's RLS policy anticipated
    this (insert_registrations_self requires auth.uid() = player_id), but the
    handler writes through the service-role client, which bypasses policies.
    """
    h = Harness()
    organiser = h.make_user("Entry Organiser", "admin")
    me = h.make_user("Entrant One")
    someone_else = h.make_user("Entrant Two")
    t = h.seed_tournament(organiser, name="Open Entries",
                          status="registration_open")

    mine = h.post("/api/tournaments/%s/registrations" % t,
                  {"type": "singles", "player_id": me}, user_id=me)
    check("a player can enter themselves", mine.status_code in (200, 201),
          detail(mine))

    theirs = h.post("/api/tournaments/%s/registrations" % t,
                    {"type": "singles", "player_id": someone_else}, user_id=me)
    check("a player cannot enter somebody else", theirs.status_code == 403,
          "%s %s" % (theirs.status_code, detail(theirs)))
    check("and is told whose job that is",
          "organiser" in detail(theirs).lower(), detail(theirs))
    check("and no entry is recorded for them",
          not any(r for r in h.db.tables.get("registrations", [])
                  if r.get("player_id") == someone_else),
          "an entry was created for another player")

    # An organiser still may, which is the whole point of the field.
    by_organiser = h.post("/api/tournaments/%s/registrations" % t,
                          {"type": "singles", "player_id": someone_else},
                          user_id=organiser)
    check("an organiser can enter somebody else",
          by_organiser.status_code in (200, 201), detail(by_organiser))


def test_a_wrong_winner_can_be_corrected():
    """
    The organiser's recovery path when the wrong player was clicked.

    A won, B was recorded, and the result was confirmed. Confirming is final on
    purpose -- it advances the winner and tells everyone -- so the way back is
    to reopen, correct the board, and confirm again. Every step is an
    organiser's, and every step is on the audit record with a reason.

    Reopening used to leave winner_name behind. The match then sat live and
    unconfirmed while still announcing the wrong player as the winner, which is
    the exact thing the organiser reopened it to be rid of.
    """
    h = Harness()
    admin = h.make_user("Correcting Organiser", "admin")
    a = h.make_user("Correct A")
    b = h.make_user("Correct B")
    t = h.seed_tournament(admin, name="Correction")
    m = h.seed_match(t, a, b, boards=1, round_name="Final", round_index=1)

    def observation(winner):
        loser = "player1" if winner == "player2" else "player2"
        return {"boardWinner": winner, "coinsRemainingWith": loser,
                "coinsRemaining": 5, "queenPocketedBy": "none",
                "queenCoveredBy": "none", "p1Penalty": 0, "p2Penalty": 0}

    h.post("/api/matches/%s/boards/1/submit" % m,
           dict(observation("player2"), p1Score=0, p2Score=0, setNumber=1,
                auditReason="scored"), user_id=admin)
    h.post("/api/matches/%s/confirm" % m, {}, user_id=admin)
    wrong = h.db.tables["matches"][0]
    if not check("the wrong result is confirmed to begin with",
                 wrong.get("result_confirmed") and wrong.get("winner_id") == b,
                 wrong.get("winner_name")):
        return

    reopened = h.post("/api/matches/%s/reopen" % m,
                      {"reason": "B was clicked by mistake; A won"}, user_id=admin)
    check("the organiser can reopen a confirmed result",
          reopened.status_code == 200, detail(reopened))
    row = h.db.tables["matches"][0]
    check("reopening puts the match back to live",
          row.get("status") == "live" and not row.get("result_confirmed"),
          "%s / %s" % (row.get("status"), row.get("result_confirmed")))
    check("reopening takes the wrong winner off the match",
          not row.get("winner_id") and not row.get("winner_name"),
          "still %r" % row.get("winner_name"))

    corrected = h.put(
        "/api/matches/%s/boards/1?reason=B%%20was%%20clicked%%20by%%20mistake&override=true" % m,
        dict(observation("player1"), boardNumber=1, setNumber=1,
             status="completed", player1Score=5, player2Score=0),
        user_id=admin)
    check("the board can then be corrected", corrected.status_code == 200,
          detail(corrected))

    again = h.post("/api/matches/%s/confirm" % m, {}, user_id=admin)
    check("and confirmed again", again.status_code == 200, detail(again))
    row = h.db.tables["matches"][0]
    check("the right player ends up the winner",
          row.get("winner_id") == a, row.get("winner_name"))

    reasons = " | ".join(e.get("reason") or "" for e in h.db.tables.get("score_audit_logs", []))
    check("the reopen is on the record with its reason",
          "clicked by mistake" in reasons, reasons[:200])
    check("and so is the override of a confirmed board",
          "OVERRIDE" in reasons, reasons[:200])


def test_correcting_a_classic_board_is_idempotent():
    """
    Correcting a board twice must not score it twice.

    Classic scoring STORES the queen-inclusive total, and the correction form
    prefills from that stored value. The correction then re-applied the queen
    to it, so a board won 21-3 with the queen went 24-3, then 27-3, then 30-3,
    climbing by the queen's worth every time anybody opened the board and
    pressed Save. Nothing refused it -- the ceiling is 60 and the
    both-reached-target check needs 29 each -- and the board WINNER stayed
    right, because that is declared rather than compared. It is the points that
    rotted, and points are the league's tie-break.
    """
    h = Harness()
    admin = h.make_user("Classic Organiser", "admin")
    t = h.seed_tournament(admin, name="Classic Scoring",
                          rules={"scoringMode": "classic", "queenPoints": 3})
    m = h.seed_match(t, h.make_user("Classic A"), h.make_user("Classic B"), boards=1)

    h.post("/api/matches/%s/boards/1/submit" % m,
           {"p1Score": 10, "p2Score": 4, "setNumber": 1,
            "queenClaimedBy": "player1", "queenCovered": True,
            "auditReason": "scored"}, user_id=admin)

    def board():
        return [b for b in h.db.tables["boards"] if b["match_id"] == m][0]

    check("ten coins and a covered queen is stored as thirteen",
          board()["player1_score"] == 13, board()["player1_score"])

    h.post("/api/matches/%s/confirm" % m, {}, user_id=admin)
    h.post("/api/matches/%s/reopen" % m, {"reason": "checking"}, user_id=admin)

    for attempt in (1, 2, 3):
        current = board()
        h.put("/api/matches/%s/boards/1?reason=restated&override=true" % m,
              {"boardNumber": 1, "setNumber": 1, "status": "completed",
               "player1Score": current["player1_score"],
               "player2Score": current["player2_score"],
               "queenClaimedBy": "player1", "queenCovered": True}, user_id=admin)
        check("restating the board unchanged leaves the score alone (pass %d)" % attempt,
              board()["player1_score"] == 13, board()["player1_score"])

    # And a real correction still moves it: twelve coins plus the queen.
    h.put("/api/matches/%s/boards/1?reason=A%%20had%%20twelve&override=true" % m,
          {"boardNumber": 1, "setNumber": 1, "status": "completed",
           "player1Score": 15, "player2Score": 4,
           "queenClaimedBy": "player1", "queenCovered": True}, user_id=admin)
    check("a real correction is still recorded", board()["player1_score"] == 15,
          board()["player1_score"])

    # Taking the queen away takes its points with it.
    h.put("/api/matches/%s/boards/1?reason=no%%20queen&override=true" % m,
          {"boardNumber": 1, "setNumber": 1, "status": "completed",
           "player1Score": 15, "player2Score": 4,
           "queenClaimedBy": "none", "queenCovered": False}, user_id=admin)
    check("removing the queen removes its points", board()["player1_score"] == 12,
          board()["player1_score"])




# ---------------------------------------------------------------------------
# Three defects in how a match is finished, each probed before it was fixed
# ---------------------------------------------------------------------------

_SCORING_RULES = {
    "queenPoints": 3, "coinsPerSide": 9, "targetScore": 29,
    "pointsForWin": 2, "pointsForDraw": 1, "pointsForLoss": 0,
    "maxBoardsPerMatch": 3, "boardsPerSet": 3,
}


def _scoring_match(rules, boards=3, stage="league"):
    h = Harness()
    admin = h.make_user("Org", "admin")
    p1 = h.make_user("Ann")
    p2 = h.make_user("Ben")
    tid = h.seed_tournament(admin, status="in_progress", rules=dict(rules))
    mid = h.seed_match(tid, p1, p2, boards=boards, stage=stage)
    h.post("/api/matches/%s/start" % mid, {}, user_id=admin)
    return h, admin, tid, mid, p1, p2


def _board(h, mid, n):
    return [x for x in h.db.rows("boards")
            if x["match_id"] == mid and x["board_number"] == n][0]


def test_a_correction_cannot_drive_a_board_negative():
    """
    Moving the queen to the other player subtracts its bonus from somebody
    who may not have that many points.

    The classic correction path takes back the award the row already carries
    before applying the stated one. That subtraction had no floor, so a board
    stored 23-5 (20 coins plus a covered queen), corrected to "Ann scored 0
    coins and the queen was Ben's", stored -3 for Ann -- and her match total
    with it. A negative board is not a thing in carrom, and the figure feeds
    net score difference, which is the league's tie-break.
    """
    h, admin, tid, mid, p1, p2 = _scoring_match(
        dict(_SCORING_RULES, scoringMode="classic"))

    r = h.put("/api/matches/%s/boards/1" % mid,
              {"boardNumber": 1, "player1Score": 20, "player2Score": 5,
               "queenClaimedBy": "player1", "queenCovered": True,
               "status": "completed"}, user_id=admin)
    if not check("a classic board with a covered queen is accepted",
                 r.status_code == 200, detail(r)):
        return
    check("the queen bonus is on the board", _board(h, mid, 1)["player1_score"] == 23,
          _board(h, mid, 1)["player1_score"])

    r = h.put("/api/matches/%s/boards/1" % mid,
              {"boardNumber": 1, "player1Score": 0, "player2Score": 5,
               "queenClaimedBy": "player2", "queenCovered": True,
               "status": "completed", "auditReason": "the queen was Ben's"},
              user_id=admin)
    check("the correction is accepted", r.status_code == 200, detail(r))

    b = _board(h, mid, 1)
    check("a corrected board is never negative",
          (b["player1_score"] or 0) >= 0 and (b["player2_score"] or 0) >= 0,
          "%s-%s" % (b["player1_score"], b["player2_score"]))

    m = [x for x in h.db.rows("matches") if x["id"] == mid][0]
    check("a corrected match total is never negative",
          (m.get("player1_total_points") or 0) >= 0
          and (m.get("player2_total_points") or 0) >= 0,
          "%s-%s" % (m.get("player1_total_points"), m.get("player2_total_points")))


def test_a_drawn_league_match_can_be_confirmed():
    """
    A level league match is a draw, and a draw is a result.

    /confirm refused every level match whatever its stage, so a drawn league
    match could not be confirmed at all -- and everything behind that was
    unreachable with it. calculate_points_table has a branch awarding
    pointsForDraw and a D column to show it; recalculate_match_scores
    deliberately leaves a level league match drawn. None of it could run.
    The match also stayed unconfirmed for good, so league_is_complete never
    became true and a league+knockout tournament could never promote.
    """
    h, admin, tid, mid, p1, p2 = _scoring_match(
        dict(_SCORING_RULES, scoringMode="classic", boardsPerSet=2), boards=2)
    for n, (a, b, w) in enumerate(((10, 5, "player1"), (5, 10, "player2")), start=1):
        h.put("/api/matches/%s/boards/%d" % (mid, n),
              {"boardNumber": n, "player1Score": a, "player2Score": b,
               "boardWinner": w, "status": "completed"}, user_id=admin)

    r = h.post("/api/matches/%s/confirm" % mid, {}, user_id=admin)
    if not check("a drawn league match can be confirmed", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return

    m = [x for x in h.db.rows("matches") if x["id"] == mid][0]
    check("the confirmed draw has no winner", not m.get("winner_id"), m.get("winner_id"))
    check("the confirmed draw is recorded as confirmed",
          m.get("result_confirmed") is True, m.get("result_confirmed"))
    check("the confirmed draw is completed", m.get("status") == "completed",
          m.get("status"))

    st = body(h.get("/api/standings/%s" % tid, admin))
    rows = (st.get("categories") or [{}])[0].get("standings") or []
    check("both players are shown as having drawn",
          sum(r.get("drawn") or 0 for r in rows) == 2,
          [(r.get("participantName"), r.get("drawn")) for r in rows])
    check("pointsForDraw is actually awarded",
          sum(r.get("points") or 0 for r in rows) == 2,
          [(r.get("participantName"), r.get("points")) for r in rows])
    check("neither player is credited with a win",
          sum(r.get("won") or 0 for r in rows) == 0,
          [(r.get("participantName"), r.get("won")) for r in rows])


def test_a_knockout_still_cannot_be_left_drawn():
    """The other half of the same rule: a bracket cannot advance a draw."""
    h, admin, tid, mid, p1, p2 = _scoring_match(
        dict(_SCORING_RULES, scoringMode="classic", boardsPerSet=2),
        boards=2, stage="knockout")
    for n, (a, b, w) in enumerate(((10, 5, "player1"), (5, 10, "player2")), start=1):
        h.put("/api/matches/%s/boards/%d" % (mid, n),
              {"boardNumber": n, "player1Score": a, "player2Score": b,
               "boardWinner": w, "status": "completed"}, user_id=admin)

    r = h.post("/api/matches/%s/confirm" % mid, {}, user_id=admin)
    check("a level knockout match is still refused", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))
    check("the refusal explains there is no winner",
          "no winner" in detail(r).lower(), detail(r))


def test_the_tie_break_ruling_needs_an_actual_tie():
    """
    The route checked that the named winner was one of the two players and
    that a reason was given -- never that there was a tie. So it would take a
    decided match and hand it to the other player, leaving the board wins and
    points saying the opposite.
    """
    h, admin, tid, mid, p1, p2 = _scoring_match(
        dict(_SCORING_RULES, scoringMode="classic"))
    for n in (1, 2):
        h.put("/api/matches/%s/boards/%d" % (mid, n),
              {"boardNumber": n, "player1Score": 10, "player2Score": 2,
               "boardWinner": "player1", "status": "completed"}, user_id=admin)

    m = [x for x in h.db.rows("matches") if x["id"] == mid][0]
    if not check("the match is decided 2-0", m.get("player1_board_wins") == 2,
                 m.get("player1_board_wins")):
        return

    r = h.post("/api/matches/%s/tie-break" % mid,
               {"winnerId": p2, "reason": "handing it to the loser"}, user_id=admin)
    check("a decided match cannot be ruled on", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))
    check("the refusal names who is ahead", "ahead" in detail(r).lower(), detail(r))

    m2 = [x for x in h.db.rows("matches") if x["id"] == mid][0]
    check("the winner of a decided match is not overwritten",
          m2.get("winner_id") != p2, m2.get("winner_id"))

    # And a genuinely level match is still rulable, which is what the route
    # exists for.
    h2, admin2, tid2, mid2, q1, q2 = _scoring_match(
        dict(_SCORING_RULES, scoringMode="classic", boardsPerSet=2), boards=2,
        stage="knockout")
    for n, (a, b, w) in enumerate(((10, 5, "player1"), (5, 10, "player2")), start=1):
        h2.put("/api/matches/%s/boards/%d" % (mid2, n),
               {"boardNumber": n, "player1Score": a, "player2Score": b,
                "boardWinner": w, "status": "completed"}, user_id=admin2)
    r = h2.post("/api/matches/%s/tie-break" % mid2,
                {"winnerId": q1, "reason": "sudden death board"}, user_id=admin2)
    check("a genuinely level match can still be ruled on", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))


SUITES = [
    ("identity branches", test_identity_branches),
    ("profile-less admin scoring", test_profileless_admin_scoring),
    ("player creation without the trigger", test_player_creation_without_trigger),
    ("access control matrix", test_access_control_matrix),
    ("admin-only routes", test_admin_only_routes),
    ("player directory privacy", test_player_directory_privacy),
    ("idempotent submission", test_idempotent_submission),
    ("out-of-range score", test_out_of_range_score_rejected),
    ("forgot password origin", test_forgot_password_origin),
    ("health", test_health_reports_schema_state),
    ("no negative board", test_a_correction_cannot_drive_a_board_negative),
    ("drawn league confirms", test_a_drawn_league_match_can_be_confirmed),
    ("knockout cannot draw", test_a_knockout_still_cannot_be_left_drawn),
    ("tie-break needs a tie", test_the_tie_break_ruling_needs_an_actual_tie),
    ("multi-set boards survive the list", test_multi_set_boards_survive_the_list),
    ("rescheduling preserves results", test_reschedule_preserves_results),
    ("create then publish", test_create_then_publish),
    ("registration roles", test_registration_roles),
    ("sign-in role must match", test_signin_role_must_match),
    ("who am I follows the profile", test_me_follows_the_profile),
    ("players enter only themselves", test_players_can_only_enter_themselves),
    ("a wrong winner can be corrected", test_a_wrong_winner_can_be_corrected),
    ("classic corrections are idempotent", test_correcting_a_classic_board_is_idempotent),
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
    print("integration suite (real app, in-memory database)")
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
