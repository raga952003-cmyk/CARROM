"""
Tournament lifecycle: the five verbs, over HTTP, against the in-memory database.

    POST /tournaments/{id}/open-registration
    POST /tournaments/{id}/close-registration
    POST /tournaments/{id}/start
    POST /tournaments/{id}/complete
    POST /tournaments/{id}/cancel

Technique: mostly BLACK BOX along the organiser's path -- open, close, draw,
start, play, complete -- with WHITE BOX cases aimed at the branches that were
identified by reading the code: the fixtures guard on /start, the "every match
settled" rule and champion derivation on /complete, the blank-reason refusal
on /cancel, the owner-only access string, and the degraded paths for a
database that has not had migration 012 applied.

The degraded cases wrap the fake's `tournaments` table so that the lifecycle
columns raise the same 42703 the real database raises for an unknown column,
and (for one case) so that the CHECK constraint refuses 'cancelled' the way
the original schema does. fakedb itself knows nothing about columns or CHECKs,
so this is the only way to reach those branches offline.

    python tests/offline/test_lifecycle.py
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness                       # noqa: E402
from fakedb import PostgrestError                 # noqa: E402
import app.routers.tournaments as tournaments_router   # noqa: E402

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

SCORE = {"p1Score": 0, "p2Score": 0, "setNumber": 1, "boardWinner": "player1",
         "coinsRemainingWith": "player2", "coinsRemaining": 4,
         "queenPocketedBy": "none", "queenCoveredBy": "none"}

VERBS = (
    ("open-registration", {}),
    ("close-registration", {}),
    ("start", {}),
    ("complete", {}),
    ("cancel", {"reason": "Testing"}),
)


def tournament_payload(fmt):
    return {
        "name": "Lifecycle %s" % fmt, "description": "", "category": "singles",
        "format": fmt,
        "registrationStartDate": "2026-01-01", "registrationEndDate": "2026-02-01",
        "tournamentStartDate": "2026-03-01", "tournamentEndDate": "2026-03-02",
        "venue": "Hall A", "city": "Chennai",
        "numberOfBoards": 4, "entryFee": 0,
        "rules": dict(RULES), "status": "draft",
    }


def verb(h, tid, name, payload, user_id):
    return h.post("/api/tournaments/%s/%s" % (tid, name), payload, user_id=user_id)


def status_of(h, tid):
    rows = [t for t in h.db.rows("tournaments") if t["id"] == tid]
    return rows[0].get("status") if rows else None


def row_of(h, tid):
    rows = [t for t in h.db.rows("tournaments") if t["id"] == tid]
    return rows[0] if rows else {}


def create(h, admin, fmt="knockout", entrants=4, draw=False):
    """
    A draft tournament with `entrants` approved singles players, through the
    API. Entries made by an admin are approved on the spot, so no approval
    step is needed. The draw is generated when asked for -- while the
    tournament is still a draft, which is a path the organiser really takes.
    """
    r = h.post("/api/tournaments", tournament_payload(fmt), user_id=admin)
    if not check("a tournament can be created for the lifecycle", r.status_code == 200,
                 "%s %s" % (r.status_code, detail(r))):
        return None, []
    tid = body(r).get("id")

    players = []
    for i in range(entrants):
        rp = h.post("/api/players", {"name": "Entrant %d" % (i + 1),
                                     "email": "lc%d@carrom.example.com" % i,
                                     "rating": 1500 + i}, user_id=admin)
        if rp.status_code != 200:
            check("every entrant can be added", False, detail(rp))
            return None, []
        pid = body(rp).get("id")
        players.append(pid)
        rr = h.post("/api/tournaments/%s/registrations" % tid,
                    {"type": "singles", "playerId": pid}, user_id=admin)
        if rr.status_code != 200:
            check("every entrant can be entered", False, detail(rr))
            return None, []

    if draw:
        rf = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
        if not check("the draw can be generated", rf.status_code == 200,
                     "%s %s" % (rf.status_code, detail(rf))):
            return None, []
    return tid, players


def finished(m):
    """The same reading of "settled" the router uses."""
    return bool(m.get("result_confirmed") or m.get("walkover_by")
                or m.get("walkover") or m.get("status") == "cancelled")


def play_everything(h, admin, tid):
    """
    Score and confirm every match that has two named sides, round by round,
    player 1 winning each board. Matches already settled -- confirmed, a
    walkover, cancelled -- are left as they are.
    """
    for _round in range(8):
        pending = [m for m in h.db.rows("matches")
                   if m["tournament_id"] == tid
                   and m.get("player1_id") and m.get("player2_id")
                   and not finished(m)]
        if not pending:
            return
        for m in pending:
            boards = sorted([b for b in h.db.rows("boards") if b["match_id"] == m["id"]],
                            key=lambda b: b["board_number"])
            for b in boards:
                h.post("/api/matches/%s/boards/%s/submit" % (m["id"], b["board_number"]),
                       dict(SCORE, setNumber=b.get("set_number") or 1), user_id=admin)
            h.post("/api/matches/%s/confirm" % m["id"], {}, user_id=admin)


def run_to_in_progress(h, admin, tid):
    """draft (with a draw) -> registration_open -> registration_closed -> in_progress."""
    for name in ("open-registration", "close-registration", "start"):
        r = verb(h, tid, name, {}, admin)
        if not check("the tournament can be walked to in_progress", r.status_code == 200,
                     "%s -> %s %s" % (name, r.status_code, detail(r))):
            return False
    return True


# ---------------------------------------------------------------------------
# A database without migration 012
# ---------------------------------------------------------------------------

LIFECYCLE_COLUMNS = {"champion_id", "champion_name", "completed_at",
                     "cancelled_at", "cancel_reason"}
ORIGINAL_STATUSES = {"draft", "registration_open", "registration_closed",
                     "scheduled", "ongoing", "completed"}


class _Pre012Table:
    """
    The `tournaments` table as a database without migration 012 presents it:
    the lifecycle columns do not exist, and -- when `legacy_check` is set --
    the status CHECK is the original schema's, which never heard of
    'cancelled', 'in_progress' or 'fixture_published'.
    """

    def __init__(self, inner, legacy_check=False):
        self.inner = inner
        self.legacy_check = legacy_check

    def select(self, columns="*", **kw):
        if columns.strip() in LIFECYCLE_COLUMNS:
            raise PostgrestError("column tournaments.%s does not exist" % columns, "42703")
        return self.inner.select(columns, **kw)

    def update(self, payload, **kw):
        bad = LIFECYCLE_COLUMNS & set(payload or {})
        if bad:
            raise PostgrestError("column tournaments.%s does not exist" % sorted(bad)[0], "42703")
        if self.legacy_check and (payload or {}).get("status") not in ORIGINAL_STATUSES | {None}:
            raise PostgrestError(
                'new row for relation "tournaments" violates check constraint '
                '"tournaments_status_check"', "23514")
        return self.inner.update(payload, **kw)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def without_migration_012(h, legacy_check=False):
    original = h.db.table

    def table(name):
        t = original(name)
        return _Pre012Table(t, legacy_check=legacy_check) if name == "tournaments" else t

    h.db.table = table
    # The probe cache is module-level and a positive answer is kept for good,
    # so a scenario that removes the columns has to forget what earlier ones
    # learned.
    tournaments_router._lifecycle_columns.clear()


def restore_probe():
    tournaments_router._lifecycle_columns.clear()


# ---------------------------------------------------------------------------
# The organiser's path, end to end
# ---------------------------------------------------------------------------

def test_full_lifecycle():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid, players = create(h, admin, "knockout", entrants=4)
    if not tid:
        return

    r = verb(h, tid, "open-registration", {}, admin)
    check("a draft can be opened for registration",
          r.status_code == 200 and body(r).get("status") == "registration_open",
          "%s %s" % (r.status_code, detail(r)))

    g = h.get("/api/tournaments/%s" % tid, admin)
    if r.status_code == 200 and g.status_code == 200:
        check("a lifecycle verb answers with the same shape as GET /tournaments/{id}",
              set(body(r).keys()) == set(body(g).keys()),
              "verb-only=%s get-only=%s" % (sorted(set(body(r)) - set(body(g))),
                                            sorted(set(body(g)) - set(body(r)))))

    r = verb(h, tid, "start", {}, admin)
    check("a tournament cannot be started while registration is open",
          r.status_code == 409, "%s %s" % (r.status_code, detail(r)))

    r = verb(h, tid, "close-registration", {}, admin)
    check("registration can be closed",
          r.status_code == 200 and body(r).get("status") == "registration_closed",
          "%s %s" % (r.status_code, detail(r)))

    # No draw yet.
    r = verb(h, tid, "start", {}, admin)
    check("starting without fixtures is refused", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))
    check("the refusal tells the organiser to generate the draw first",
          "fixture" in detail(r).lower() or "draw" in detail(r).lower(), detail(r))
    check("a refused start leaves the status where it was",
          status_of(h, tid) == "registration_closed", status_of(h, tid))

    r = verb(h, tid, "complete", {}, admin)
    check("a tournament that has not started cannot be completed",
          r.status_code == 409, "%s %s" % (r.status_code, detail(r)))

    rf = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    check("the draw can be generated once registration is closed",
          rf.status_code == 200, "%s %s" % (rf.status_code, detail(rf)))
    check("generating the draw publishes the fixtures",
          status_of(h, tid) == "fixture_published", status_of(h, tid))

    r = verb(h, tid, "start", {}, admin)
    check("a tournament with fixtures can be started",
          r.status_code == 200 and body(r).get("status") == "in_progress",
          "%s %s" % (r.status_code, detail(r)))

    r = verb(h, tid, "start", {}, admin)
    check("starting twice is refused rather than repeated",
          r.status_code == 409 and "already" in detail(r).lower(),
          "%s %s" % (r.status_code, detail(r)))

    matches = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    r = verb(h, tid, "complete", {}, admin)
    check("completing with unconfirmed matches is refused", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))
    check("the refusal counts the matches still to be settled",
          ("%d match" % len(matches)) in detail(r), detail(r))
    check("the refusal is in words, not a dump", "{" not in detail(r), detail(r))
    check("a refused completion sends nobody a notice",
          not [n for n in h.db.rows("notifications") if n.get("type") == "tournament_completed"],
          len(h.db.rows("notifications")))

    play_everything(h, admin, tid)
    finals = [m for m in h.db.rows("matches")
              if m["tournament_id"] == tid and not m.get("next_match_id")]
    if not check("the bracket ends in one final with a winner",
                 len(finals) == 1 and finals[0].get("winner_id"),
                 [(f.get("round_name"), f.get("winner_id")) for f in finals]):
        return
    final = finals[0]

    r = verb(h, tid, "complete", {}, admin)
    check("a fully played tournament can be completed",
          r.status_code == 200 and body(r).get("status") == "completed",
          "%s %s" % (r.status_code, detail(r)))
    if r.status_code != 200:
        return
    check("the champion is the winner of the final",
          body(r).get("championId") == final["winner_id"],
          "championId=%s final winner=%s" % (body(r).get("championId"), final["winner_id"]))
    check("the champion is named", body(r).get("championName") == final.get("winner_name"),
          body(r).get("championName"))
    check("the completion time is recorded", bool(body(r).get("completedAt")),
          body(r).get("completedAt"))
    check("the champion is on the tournament row",
          row_of(h, tid).get("champion_id") == final["winner_id"], row_of(h, tid))

    notes = [n for n in h.db.rows("notifications") if n.get("type") == "tournament_completed"]
    check("every entrant is told the tournament is complete",
          set(n.get("profile_id") for n in notes) >= set(players),
          "told=%d entrants=%d" % (len(notes), len(players)))
    check("the completion notice names the champion",
          notes and all(final.get("winner_name") in (n.get("message") or "") for n in notes),
          [n.get("message") for n in notes][:2])

    audits = [a for a in h.db.rows("audit_logs") if a.get("action") == "tournament.complete"]
    check("completing the tournament is audited once", len(audits) == 1, len(audits))
    if audits:
        check("the audit record carries the champion",
              (audits[0].get("new_state") or {}).get("champion_id") == final["winner_id"],
              audits[0].get("new_state"))

    for name, payload in VERBS:
        r = verb(h, tid, name, payload, admin)
        check("a completed tournament refuses every further move",
              r.status_code == 409, "%s -> %s %s" % (name, r.status_code, detail(r)))
    check("a completed tournament stays completed", status_of(h, tid) == "completed",
          status_of(h, tid))

    r = h.client.get("/api/tournaments/%s" % tid)
    check("a spectator can read the completed tournament and its champion",
          r.status_code == 200 and body(r).get("championId") == final["winner_id"],
          "%s %s" % (r.status_code, str(body(r))[:120]))


# ---------------------------------------------------------------------------
# A draw made while still a draft: registration_closed -> in_progress directly
# ---------------------------------------------------------------------------

def test_start_from_closed_registration_with_a_draw():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid, _players = create(h, admin, "knockout", entrants=4, draw=True)
    if not tid:
        return
    check("a draw made on a draft leaves it a draft", status_of(h, tid) == "draft",
          status_of(h, tid))
    if run_to_in_progress(h, admin, tid):
        check("a closed registration with a draw can go straight to in_progress",
              status_of(h, tid) == "in_progress", status_of(h, tid))


# ---------------------------------------------------------------------------
# Champion of a league: rank 1 of the points table
# ---------------------------------------------------------------------------

def test_round_robin_champion():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid, _players = create(h, admin, "round_robin", entrants=4, draw=True)
    if not tid or not run_to_in_progress(h, admin, tid):
        return

    play_everything(h, admin, tid)
    table = body(h.get("/api/standings/%s" % tid, admin))
    top = (table.get("standings") or [{}])[0] if isinstance(table, dict) else {}

    r = verb(h, tid, "complete", {}, admin)
    check("a fully played league can be completed",
          r.status_code == 200 and body(r).get("status") == "completed",
          "%s %s" % (r.status_code, detail(r)))
    if r.status_code == 200:
        check("the league champion is the top of the points table",
              body(r).get("championId") == top.get("participantId")
              and body(r).get("championName") == top.get("participantName"),
              "champion=%s/%s table=%s/%s" % (body(r).get("championId"),
                                              body(r).get("championName"),
                                              top.get("participantId"),
                                              top.get("participantName")))


# ---------------------------------------------------------------------------
# Walkovers and cancelled matches count as settled
# ---------------------------------------------------------------------------

def test_walkovers_and_cancelled_matches_are_settled():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid, _players = create(h, admin, "round_robin", entrants=4, draw=True)
    if not tid or not run_to_in_progress(h, admin, tid):
        return

    matches = sorted([m for m in h.db.rows("matches") if m["tournament_id"] == tid],
                     key=lambda m: m.get("match_number") or 0)
    if not check("a four-entrant league has six matches", len(matches) == 6, len(matches)):
        return

    walkover, struck = matches[0], matches[1]
    r = h.post("/api/matches/%s/walkover" % walkover["id"],
               {"winnerId": walkover["player1_id"], "reason": "Did not arrive"}, user_id=admin)
    check("a walkover can be recorded", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    for row in h.db.tables["matches"]:
        if row["id"] == struck["id"]:
            row["status"] = "cancelled"

    r = verb(h, tid, "complete", {}, admin)
    check("four unplayed matches still block completion",
          r.status_code == 409 and "4 match" in detail(r), "%s %s" % (r.status_code, detail(r)))

    play_everything(h, admin, tid)
    settled = [m for m in h.db.rows("matches") if m["tournament_id"] == tid]
    check("the walkover was left unconfirmed rather than played",
          not [m for m in settled if m["id"] == walkover["id"]][0].get("result_confirmed"),
          [m for m in settled if m["id"] == walkover["id"]][0])

    r = verb(h, tid, "complete", {}, admin)
    check("a walkover and a cancelled match do not hold up completion",
          r.status_code == 200 and body(r).get("status") == "completed",
          "%s %s" % (r.status_code, detail(r)))


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------

def test_cancel():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid, players = create(h, admin, "knockout", entrants=4, draw=True)
    if not tid:
        return

    r = verb(h, tid, "cancel", {}, admin)
    check("cancelling without a reason is refused", r.status_code == 422,
          "%s %s" % (r.status_code, detail(r)))
    r = verb(h, tid, "cancel", {"reason": "   "}, admin)
    check("a blank reason is refused", r.status_code == 422,
          "%s %s" % (r.status_code, detail(r)))
    check("the refusal says a reason is needed", "reason" in detail(r).lower(), detail(r))
    check("a refused cancellation changes nothing", status_of(h, tid) == "draft",
          status_of(h, tid))
    check("a refused cancellation tells nobody",
          not [n for n in h.db.rows("notifications") if n.get("type") == "tournament_cancelled"],
          len(h.db.rows("notifications")))

    r = verb(h, tid, "cancel", {"reason": "Venue flooded"}, admin)
    check("a draft can be cancelled with a reason",
          r.status_code == 200 and body(r).get("status") == "cancelled",
          "%s %s" % (r.status_code, detail(r)))
    if r.status_code != 200:
        return
    check("the reason is on the record", body(r).get("cancelReason") == "Venue flooded",
          body(r).get("cancelReason"))
    check("the cancellation time is recorded", bool(body(r).get("cancelledAt")),
          body(r).get("cancelledAt"))

    notes = [n for n in h.db.rows("notifications") if n.get("type") == "tournament_cancelled"]
    check("every entrant is told the tournament is cancelled",
          set(n.get("profile_id") for n in notes) >= set(players),
          "told=%d entrants=%d" % (len(notes), len(players)))
    check("the cancellation notice carries the reason",
          notes and all("Venue flooded" in (n.get("message") or "") for n in notes),
          [n.get("message") for n in notes][:2])
    audits = [a for a in h.db.rows("audit_logs") if a.get("action") == "tournament.cancel"]
    check("cancelling is audited with the reason",
          len(audits) == 1 and (audits[0].get("request_context") or {}).get("reason") == "Venue flooded",
          audits)

    for name, payload in VERBS:
        r = verb(h, tid, name, payload, admin)
        check("a cancelled tournament refuses every further move",
              r.status_code == 409, "%s -> %s %s" % (name, r.status_code, detail(r)))

    r = h.client.get("/api/tournaments")
    listed = [t.get("id") for t in body(r)] if isinstance(body(r), list) else []
    check("a cancelled tournament is still listed, so people can see it was called off",
          tid in listed, listed)

    # Mid-event as well: the common case for a real cancellation.
    h2 = Harness()
    admin2 = h2.make_user("Organiser", "admin")
    tid2 = h2.seed_tournament(owner_id=admin2, status="in_progress")
    r = verb(h2, tid2, "cancel", {"reason": "Rain"}, admin2)
    check("a running tournament can be cancelled",
          r.status_code == 200 and body(r).get("status") == "cancelled",
          "%s %s" % (r.status_code, detail(r)))


# ---------------------------------------------------------------------------
# Who may do this
# ---------------------------------------------------------------------------

def test_permissions():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    scorer = h.make_user("Scorer", "admin")
    manager = h.make_user("Manager", "admin")
    stranger = h.make_user("Stranger", "admin")
    player = h.make_user("Player", "player")
    tid = h.seed_tournament(owner_id=owner, status="draft")
    h.db.seed("tournament_access", [
        {"id": "acc-scorer", "tournament_id": tid, "user_id": scorer,
         "access_role": "scorer", "status": "approved", "decided_by": owner},
        {"id": "acc-manager", "tournament_id": tid, "user_id": manager,
         "access_role": "manager", "status": "approved", "decided_by": owner},
    ])

    for name, payload in VERBS:
        r = verb(h, tid, name, payload, scorer)
        check("a scorer is refused every lifecycle verb", r.status_code == 403,
              "%s -> %s %s" % (name, r.status_code, detail(r)))
        check("the scorer is told whose tournament it is",
              r.status_code != 403 or "owner" in detail(r).lower(), detail(r))
        r = verb(h, tid, name, payload, stranger)
        check("an uninvited admin is refused every lifecycle verb", r.status_code == 403,
              "%s -> %s %s" % (name, r.status_code, detail(r)))
        r = verb(h, tid, name, payload, player)
        check("a player is refused every lifecycle verb", r.status_code == 403,
              "%s -> %s %s" % (name, r.status_code, detail(r)))
        r = h.client.post("/api/tournaments/%s/%s" % (tid, name), json=payload)
        check("an anonymous caller is refused every lifecycle verb",
              r.status_code in (401, 403), "%s -> %s %s" % (name, r.status_code, detail(r)))
    check("refused callers change nothing", status_of(h, tid) == "draft", status_of(h, tid))

    r = verb(h, tid, "open-registration", {}, manager)
    check("an approved manager can run the lifecycle", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    r = verb(h, tid, "close-registration", {}, owner)
    check("the owner can run the lifecycle", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    r = verb(h, "99999999-9999-9999-9999-999999999999", "start", {}, owner)
    check("a verb on an unknown tournament is a 404", r.status_code == 404,
          "%s %s" % (r.status_code, detail(r)))


# ---------------------------------------------------------------------------
# The transition table, through the verbs
# ---------------------------------------------------------------------------

def test_illegal_transitions():
    h = Harness()
    admin = h.make_user("Organiser", "admin")

    cases = (
        ("draft", "close-registration"),
        ("draft", "start"),
        ("draft", "complete"),
        ("registration_open", "complete"),
        ("registration_open", "start"),
        ("registration_closed", "complete"),
        ("fixture_published", "complete"),
        ("fixture_published", "close-registration"),
        ("in_progress", "open-registration"),
        ("in_progress", "close-registration"),
        # The legacy spellings are read as their modern names.
        ("ongoing", "open-registration"),
        ("scheduled", "complete"),
    )
    for i, (state, name) in enumerate(cases):
        tid = h.seed_tournament(owner_id=admin, status=state,
                                id="55555555-5555-5555-5555-5555555555%02d" % i)
        r = verb(h, tid, name, {}, admin)
        check("an illegal lifecycle move is refused with 409", r.status_code == 409,
              "%s -> %s: %s %s" % (state, name, r.status_code, detail(r)))
        check("the refusal names the state and what is allowed from it",
              state.split("_")[0] in detail(r).lower() or "already" in detail(r).lower()
              or "fixture" in detail(r).lower(),
              "%s -> %s: %s" % (state, name, detail(r)))
        check("an illegal move leaves the status untouched", status_of(h, tid) == state,
              "%s -> %s: now %s" % (state, name, status_of(h, tid)))

    tid = h.seed_tournament(owner_id=admin, status="registration_open",
                            id="55555555-5555-5555-5555-555555555599")
    r = verb(h, tid, "open-registration", {}, admin)
    check("re-opening an open registration is refused, not repeated",
          r.status_code == 409 and "already" in detail(r).lower(),
          "%s %s" % (r.status_code, detail(r)))


# ---------------------------------------------------------------------------
# PUT /tournaments/{id} still accepts a status, through set_tournament_status
# ---------------------------------------------------------------------------

def test_put_status_compatibility():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin, status="draft")

    r = h.put("/api/tournaments/%s" % tid, {"status": "registration_open"}, user_id=admin)
    check("PUT with a legal status still moves the tournament",
          r.status_code == 200 and status_of(h, tid) == "registration_open",
          "%s %s now=%s" % (r.status_code, detail(r), status_of(h, tid)))
    audits = [a for a in h.db.rows("audit_logs") if a.get("action") == "tournament.update"]
    check("a PUT status change is audited as a changed field",
          audits and "status" in ((audits[-1].get("request_context") or {}).get("changed_fields") or []),
          audits[-1].get("request_context") if audits else None)

    r = h.put("/api/tournaments/%s" % tid, {"status": "completed"}, user_id=admin)
    check("PUT with an illegal status is refused with 409", r.status_code == 409,
          "%s %s" % (r.status_code, detail(r)))

    r = h.put("/api/tournaments/%s" % tid,
              {"name": "Renamed", "status": "registration_open"}, user_id=admin)
    check("PUT that re-asserts the current status is a plain update",
          r.status_code == 200 and row_of(h, tid).get("name") == "Renamed",
          "%s %s" % (r.status_code, detail(r)))

    # The original schema's CHECK: in_progress is refused, ongoing is written.
    h2 = Harness()
    admin2 = h2.make_user("Organiser", "admin")
    tid2 = h2.seed_tournament(owner_id=admin2, status="fixture_published")
    without_migration_012(h2, legacy_check=True)
    try:
        r = h2.put("/api/tournaments/%s" % tid2, {"status": "in_progress"}, user_id=admin2)
        check("on an un-migrated database PUT writes the legacy synonym instead of failing",
              r.status_code == 200 and status_of(h2, tid2) == "ongoing",
              "%s %s now=%s" % (r.status_code, detail(r), status_of(h2, tid2)))
        check("the response reports the status actually stored",
              body(r).get("status") == "ongoing" if r.status_code == 200 else False,
              body(r).get("status") if isinstance(body(r), dict) else body(r))
    finally:
        restore_probe()


# ---------------------------------------------------------------------------
# Degraded: no migration 012
# ---------------------------------------------------------------------------

def test_without_migration_012_still_moves_status():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid, players = create(h, admin, "knockout", entrants=4, draw=True)
    if not tid:
        return
    without_migration_012(h)
    try:
        if not run_to_in_progress(h, admin, tid):
            return
        play_everything(h, admin, tid)
        final = [m for m in h.db.rows("matches")
                 if m["tournament_id"] == tid and not m.get("next_match_id")][0]

        r = verb(h, tid, "complete", {}, admin)
        check("without migration 012 a tournament can still be completed",
              r.status_code == 200 and status_of(h, tid) == "completed",
              "%s %s" % (r.status_code, detail(r)))
        if r.status_code != 200:
            return
        check("without the columns, no champion field is invented on the wire",
              "championId" not in body(r) and "completedAt" not in body(r),
              sorted(k for k in body(r) if "champion" in k.lower() or "completed" in k.lower()))
        check("without the columns, nothing was written to them",
              "champion_id" not in row_of(h, tid), sorted(row_of(h, tid).keys()))
        audits = [a for a in h.db.rows("audit_logs") if a.get("action") == "tournament.complete"]
        check("the champion is still on file in the audit trail",
              audits and (audits[0].get("new_state") or {}).get("champion_id") == final["winner_id"],
              audits[0].get("new_state") if audits else None)
        check("the audit trail says the columns were not recorded",
              audits and (audits[0].get("request_context") or {}).get("lifecycle_columns_recorded") is False,
              audits[0].get("request_context") if audits else None)
        notes = [n for n in h.db.rows("notifications") if n.get("type") == "tournament_completed"]
        check("participants are still told, and told who won",
              set(n.get("profile_id") for n in notes) >= set(players)
              and all(final.get("winner_name") in (n.get("message") or "") for n in notes),
              "told=%d" % len(notes))

        h2 = Harness()
        admin2 = h2.make_user("Organiser", "admin")
        tid2 = h2.seed_tournament(owner_id=admin2, status="registration_open")
        without_migration_012(h2)
        r = verb(h2, tid2, "cancel", {"reason": "Storm"}, admin2)
        check("without migration 012 a tournament can still be cancelled",
              r.status_code == 200 and status_of(h2, tid2) == "cancelled",
              "%s %s" % (r.status_code, detail(r)))
        check("the reason survives in the audit trail",
              [a for a in h2.db.rows("audit_logs")
               if a.get("action") == "tournament.cancel"
               and (a.get("new_state") or {}).get("cancel_reason") == "Storm"],
              h2.db.rows("audit_logs"))
    finally:
        restore_probe()


def test_check_constraint_refuses_cancelled():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin, status="draft")
    without_migration_012(h, legacy_check=True)
    try:
        r = verb(h, tid, "cancel", {"reason": "Storm"}, admin)
        check("a CHECK that refuses 'cancelled' is reported as 503, not 400",
              r.status_code == 503, "%s %s" % (r.status_code, detail(r)))
        check("the 503 names the migration to apply", "012_lifecycle.sql" in detail(r), detail(r))
        check("a refused cancellation leaves the status where it was",
              status_of(h, tid) == "draft", status_of(h, tid))
        check("a refused cancellation tells nobody",
              not [n for n in h.db.rows("notifications") if n.get("type") == "tournament_cancelled"],
              len(h.db.rows("notifications")))

        # The legacy names still work for the states that have them.
        r = verb(h, tid, "open-registration", {}, admin)
        check("states the original schema knows are unaffected",
              r.status_code == 200 and status_of(h, tid) == "registration_open",
              "%s %s" % (r.status_code, detail(r)))
    finally:
        restore_probe()


SUITES = [
    ("full lifecycle", test_full_lifecycle),
    ("start from closed registration with a draw", test_start_from_closed_registration_with_a_draw),
    ("round robin champion", test_round_robin_champion),
    ("walkovers and cancelled matches", test_walkovers_and_cancelled_matches_are_settled),
    ("cancel", test_cancel),
    ("permissions", test_permissions),
    ("illegal transitions", test_illegal_transitions),
    ("PUT status compatibility", test_put_status_compatibility),
    ("without migration 012", test_without_migration_012_still_moves_status),
    ("CHECK refuses cancelled", test_check_constraint_refuses_cancelled),
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
    print("lifecycle suite (real app, in-memory database)")
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
